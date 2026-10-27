"""Ensaio separado; nenhuma evidência histórica fabrica eventos de caixa."""
from datetime import date
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import json
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session
from app.persistence.database import DEFAULT_DATABASE_PATH, make_engine, transaction
from app.persistence.migrations import upgrade_database
from app.persistence.models import LegacyImportBatch, LegacySourceRow, LegacyObligation
from .legacy_reader import (APPROVED_SOURCE_SHA256, POLICY_VERSION, LegacyValidationError, controls, document, prepare_rows, read_workbook)
from .settlements import now_text

LEGACY_TABLES={"legacy_import_batches","legacy_source_rows","legacy_obligations","alembic_version"}

def ensure_trial_database(path):
    path=Path(path).resolve()
    if path==DEFAULT_DATABASE_PATH.resolve() or "trial" not in path.stem.lower():
        raise LegacyValidationError("Use um banco separado com 'trial' no nome. O banco pessoal não é destino de ensaio.")
    if path.exists():
        engine=make_engine(path)
        try:
            with engine.connect() as conn:
                for table in set(inspect(conn).get_table_names())-LEGACY_TABLES:
                    if conn.exec_driver_sql('SELECT COUNT(*) FROM "'+table.replace('"','""')+'"').scalar():
                        raise LegacyValidationError("O banco contém dados operacionais; não é um ensaio isolado.")
        finally:engine.dispose()
    return path

def exact_file(path,content):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.read_bytes()!=content:raise LegacyValidationError("Já existe uma saída diferente neste caminho; nada foi sobrescrito.")
    else:
        with path.open("xb") as stream:stream.write(content)

def extraction_document(workbook,rows):
    main=workbook["sheet"];inside={f"{col}{row['row_number']}" for row in rows for col in "BCDEFGHIJKLMNO"}
    context={"date1904":workbook["date1904"],"table_ref":workbook["table_ref"],"headers":workbook["headers"],
        "outside_table":{sheet:{ref:cell for ref,cell in cells.items() if sheet!=main or ref not in inside}
            for sheet,cells in workbook["sheets"].items()},
        "financial_grain":"observed_obligation_not_reconstructed_purchase","bank_events_created":0,"parents_settlement":"unknown",
        "cycle_policy":"simulation_of_approved_current_rules_not_historical_reconciliation"}
    return {"file_sha256":workbook["sha256"],"policy_version":POLICY_VERSION,"rows":rows,"context":context}

def import_prepared(engine,workbook,rows,original_name):
    extraction=extraction_document(workbook,rows)
    digest=sha256(document(extraction).encode("utf-8")).hexdigest();control=controls(rows)
    with transaction(engine) as session:
        previous=session.scalar(select(LegacyImportBatch).where(LegacyImportBatch.file_sha256==workbook["sha256"],
            LegacyImportBatch.policy_version==POLICY_VERSION))
        if previous:
            if previous.extraction_sha256!=digest or previous.control_json!=document(control):
                raise LegacyValidationError("O mesmo arquivo/política já foi interpretado de outra forma. Preserve o ensaio anterior.")
            identity=previous.id;created=False
        else:
            batch=LegacyImportBatch(file_sha256=workbook["sha256"],policy_version=POLICY_VERSION,original_name=original_name,
                recorded_at=now_text(),extraction_sha256=digest,control_json=document(control),context_json=document(extraction["context"]))
            session.add(batch);session.flush();identity=batch.id;created=True
            for row in rows:
                source=LegacySourceRow(batch_id=identity,sheet_name=row["sheet_name"],row_number=row["row_number"],status=row["status"],
                    source_amount_decimal=row["source_amount_decimal"],raw_json=document(row["raw"]),
                    issues_json=document(row["issues"]),proposal_json=document(row["proposal"]))
                session.add(source);session.flush()
                if row["status"]=="accepted":
                    values=dict(row["proposal"]);values["reference_on"]=date.fromisoformat(values["reference_on"])
                    values["simulated_cycle"]=date.fromisoformat(values["simulated_cycle"]) if values["simulated_cycle"] else None
                    session.add(LegacyObligation(source_row_id=source.id,**values))
            session.flush()
        verify_database(session,identity,rows,control)
    return identity,created,control,extraction

def verify_database(session,identity,rows,control):
    sources=list(session.scalars(select(LegacySourceRow).where(LegacySourceRow.batch_id==identity).order_by(LegacySourceRow.row_number)))
    if len(sources)!=len(rows):raise LegacyValidationError("Contagem de origens divergente.")
    planned={row["row_number"]:row for row in rows}
    for source in sources:
        original=planned[source.row_number]
        if (source.sheet_name,source.status,source.raw_json,source.proposal_json,source.issues_json,source.source_amount_decimal)!=(original["sheet_name"],original["status"],document(original["raw"]),document(original["proposal"]),document(original["issues"]),original["source_amount_decimal"]):
            raise LegacyValidationError("Uma origem/transformação diverge da extração.")
    records=session.execute(select(LegacyObligation,LegacySourceRow.row_number).join(LegacySourceRow).where(LegacySourceRow.batch_id==identity)).all()
    accepted=[row for row in rows if row["status"]=="accepted"]
    if len(records)!=len(accepted) or sum(record.amount_cents for record,_ in records)!=control["accepted_cents"]:
        raise LegacyValidationError("Contagens ou valores aceitos divergentes.")
    actual_groups={}
    for record,number in records:
        proposal=planned[number]["proposal"]
        for field,expected in proposal.items():
            actual=getattr(record,field)
            if type(actual) is date:actual=actual.isoformat()
            if actual!=expected:raise LegacyValidationError("Uma obrigação diverge do mapeamento aprovado de origem.")
        key=document([record.reference_on.isoformat()[:7],record.kind,record.responsible_code,record.card_code or record.payment_method])
        actual_groups[key]=actual_groups.get(key,0)+record.amount_cents
    for key,group in control["groups"].items():
        if actual_groups.get(key,0)!=group["accepted_cents"]:
            raise LegacyValidationError("Valor por período/responsável/cartão/tipo divergente.")
    if session.connection().exec_driver_sql("PRAGMA foreign_key_check").all():
        raise LegacyValidationError("Há vínculos inconsistentes no ensaio.")

def report_markdown(source,database,batch_id,control,rows):
    pending=[row for row in rows if row["status"]=="pending"]
    lines=["# Relatório privado — importação de ensaio","",
        "Este banco não substitui o banco pessoal. Linhas aceitas representam obrigações observadas, sem reconstruir compras ou gerar parcelas ausentes.","",
        f"- Fonte SHA-256: {source}",f"- Banco de ensaio: {database}",f"- Lote: {batch_id}; política: {POLICY_VERSION}.",
        f"- Origens preservadas: {control['source_rows']}.",
        f"- Aceitas no modelo de obrigações observadas: {control['counts']['accepted']}.",
        f"- Pendentes: {control['counts']['pending']}.",
        f"- Excluídas por decisão aprovada: {control['counts']['excluded']}.",
        f"- Soma aceita: {control['accepted_cents']} centavos.",
        "- Eventos de caixa, repasses, saldos iniciais e novas parcelas fabricados: zero.","",
        "## Reconciliação exata","",
        "Valores abaixo estão em reais decimais de origem. Não há arredondamento oculto para zerar diferenças.","",
        f"- Soma numérica armazenada no XML: {control['source_decimal']}.",
        f"- Pendências numéricas preservadas: {control['pending_decimal']}.",
        f"- Exclusões: {control['excluded_decimal']}.",
        f"- Diferença de representação dos resultados de fórmulas constantes verificadas: {control['representation_decimal']}.",
        f"- Diferença não explicada: {control['difference_decimal']}.",
        f"- Valores de origem não numéricos: {control['non_numeric_source_amounts']}.",
        f"- Controles por período, responsável, cartão/meio e tipo: {'conferem' if control['reconciled'] else 'há limitações a revisar'}.","",
        "A equação é: XML = aceitos em centavos/100 + pendentes + excluídos + diferença de representação explicitada. Cada grupo possui o mesmo controle no arquivo JSON.","",
        "## Pendências por linha","",
        "| Linha | Valor decimal da origem | Motivos |","|---|---|---|"]
    for row in pending:lines.append(f"| {row['row_number']} | {row['source_amount_decimal'] or 'ausente'} | {', '.join(row['blockers'])} |")
    lines+=["","## Indicadores de qualidade","", "| Motivo | Registros |","|---|---|"]
    for issue,count in sorted(control["issues"].items()):lines.append(f"| {issue} | {count} |")
    lines+=["","## Como interpretar","",
        "- binary_representation_review: o XML contém uma cauda decimal próxima de centavos; não foi arredondada automaticamente.",
        "- fractional_cent: fração de centavo real, como 61,125; precisa da distribuição efetivamente cobrada.",
        "- income_or_opening_balance: confirmar se Conta Banco é receita ou saldo inicial.",
        "- sequence_not_reconstructed: preservar apenas as parcelas observadas, sem inventar a compra original.",
        "- missing_description/missing_category: ausência preservada; não há texto financeiro inventado.",
        "- parents_settlement_unknown: não há comprovação de repasse histórico.",
        "- cycle_simulation_requires_reconciliation: ciclo simulado com as regras aprovadas; conferir com faturas reais.",
        "- institution_unknown/noncredit_settlement_unknown: não inventar conta, data ou evento a partir do campo Pago.","",
        "## Decisões já aplicadas","",
        "D12 só é aplicada à fonte com hash aprovado e conteúdo conferido: origem 125 mantida, ocorrência 129 excluída do financeiro e ambas preservadas. D13 mantém 174, 236 e 345 como observações distintas; nunca há agrupamento ou renumeração automática.","",
        "A cópia source.xlsm/source.xlsx é byte a byte da fonte. extraction.json preserva células, fórmulas, caches, propostas e contexto fora da tabela. O original não foi salvo, recalculado pelo Excel nem teve macros executadas.","",
        "## Próxima revisão humana","",
        "Revisar as caudas de representação e os centavos fracionários; classificar Conta Banco; conferir agrupamentos e ciclos. Aprovação de exceções e recuperação validada precedem qualquer adoção definitiva. Não somar este ensaio aos lançamentos pessoais sem uma migração posterior aprovada.",""]
    return "\n".join(lines)

def run_trial(source_path,database_path,work_root,*,expected_sha256=APPROVED_SOURCE_SHA256):
    source_path=Path(source_path).resolve();database_path=ensure_trial_database(database_path)
    if not source_path.is_file() or source_path.suffix.lower() not in {".xlsx",".xlsm"}:
        raise LegacyValidationError("Informe uma fonte local XLSX/XLSM existente.")
    content=source_path.read_bytes();identity=sha256(content).hexdigest()
    if identity!=expected_sha256.lower():raise LegacyValidationError("O hash da fonte mudou. Não aplicar decisões antigas sem nova análise.")
    workbook=read_workbook(content);rows=prepare_rows(workbook)
    destination=Path(work_root).resolve()/identity/POLICY_VERSION
    if source_path==database_path or source_path.is_relative_to(destination):
        raise LegacyValidationError("A fonte deve ficar separada das saídas do ensaio.")
    copy_path=destination/("source"+source_path.suffix.lower())
    exact_file(copy_path,content)
    upgrade_database(database_path);engine=make_engine(database_path)
    try:batch_id,created,control,extraction=import_prepared(engine,workbook,rows,source_path.name)
    finally:engine.dispose()
    exact_file(destination/"extraction.json",document(extraction).encode("utf-8"))
    exact_file(destination/"controls.json",document(control).encode("utf-8"))
    exact_file(destination/"REPORT.md",report_markdown(identity,database_path,batch_id,control,rows).encode("utf-8"))
    if sha256(source_path.read_bytes()).hexdigest()!=identity or sha256(copy_path.read_bytes()).hexdigest()!=identity:
        raise LegacyValidationError("A identidade da fonte/cópia mudou durante o ensaio. Preserve as saídas para investigar.")
    return {"batch_id":batch_id,"created":created,"database":str(database_path),"report":str(destination/"REPORT.md"),
        "source_sha256":identity,"control":control,"original_unchanged":True}
