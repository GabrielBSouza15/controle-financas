"""Adoção explícita em outro arquivo, com backups e restauração conferida."""
import json
from hashlib import sha256
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.persistence.database import make_engine,transaction
from app.persistence.models import HistoryAdoption,HistoryBatch
from app.persistence.migrations import current_revision,head_revision
from .backup import create_backup,restore_backup,database_fingerprint
from .settlements import now_text,canonical
from .catalogs import ConflictError
from app.persistence.entries import PersistenceValidationError
from .history_candidate import summary
from .dashboard import summary as dashboard
from datetime import date

def adopt(source,destination,verification_path,backup_root,*,expected_source_sha256,accept_known_limits=False,defer_independent_backup=False):
    if accept_known_limits is not True or defer_independent_backup is not True:
        raise PersistenceValidationError("A adoção exige aceite explícito dos limites conhecidos e do adiamento do backup independente.")
    source=Path(source).resolve();destination=Path(destination).resolve();verification_path=Path(verification_path).resolve()
    if source==destination or destination.exists() or verification_path.exists() or verification_path in {source,destination}:
        raise ConflictError("Preserve as bases existentes. Adoção e conferência exigem arquivos novos e distintos.")
    if current_revision(source)!=head_revision():
        raise PersistenceValidationError("Atualize e confira a estrutura da origem antes da adoção.")
    engine=make_engine(source)
    try:
        with Session(engine) as session:
            batches=list(session.scalars(select(HistoryBatch).where(HistoryBatch.posted.is_(True))))
            if len(batches)!=1 or batches[0].source_sha256!=expected_source_sha256 or session.get(HistoryAdoption,1):
                raise ConflictError("A origem não corresponde ao candidato aprovado ou já foi adotada.")
        before=summary(engine)
        months=[date.fromisoformat(m["month"]) for m in before["months"]]
        budgets=[dashboard(engine,m,3) for m in months]
    finally:engine.dispose()
    fingerprint=database_fingerprint(source)
    bundle=create_backup(source,backup_root)
    if database_fingerprint(Path(bundle["folder"])/"database.db")!=fingerprint or database_fingerprint(source)!=fingerprint:
        raise ConflictError("A origem mudou durante o backup. Nenhuma base foi selecionada.")
    restore_backup(bundle["folder"],destination)
    if database_fingerprint(destination)!=fingerprint:
        raise PersistenceValidationError("A cópia não reproduziu a origem. Preserve os arquivos para conferência.")
    accepted={"authorized":True,"independent_backup_deferred":True,
        "unknown_payment_dates_and_repayments_preserved":True,"unconfirmed_groups_not_promoted":True,
        "cycles_require_actual_statement_evidence":True,"source_path":str(source)}
    adopted=make_engine(destination)
    try:
        with transaction(adopted) as session:
            session.add(HistoryAdoption(id=1,source_sha256=expected_source_sha256,
                source_fingerprint=sha256(canonical(fingerprint).encode()).hexdigest(),
                recorded_at=now_text(),acceptance_json=canonical(accepted)))
        after=summary(adopted)
        if after["months"]!=before["months"] or after["pending_cents"]!=before["pending_cents"] or after["source_rows"]!=before["source_rows"]:
            raise PersistenceValidationError("Totais divergentes após o aceite; não atualizar o atalho.")
        if [dashboard(adopted,m,3) for m in months]!=budgets:
            raise PersistenceValidationError("Composição divergente após adoção; não atualizar o atalho.")
    finally:adopted.dispose()
    final=create_backup(destination,backup_root)
    restore_backup(final["folder"],verification_path)
    if database_fingerprint(verification_path)!=database_fingerprint(destination) or database_fingerprint(source)!=fingerprint:
        raise PersistenceValidationError("Recuperação ou preservação divergente; não atualizar o atalho.")
    # Somente o recibo de aceite pode diferir do candidato.
    original=fingerprint["tables"];current=database_fingerprint(destination)["tables"]
    if any(current[n]!=v for n,v in original.items() if n!="history_adoption"):
        raise PersistenceValidationError("Dados anteriores divergiram na cópia principal.")
    return {"adopted":True,"source":str(source),"destination":str(destination),"verification_path":str(verification_path),
        "before_backup":bundle["folder"],"after_backup":final["folder"],"previous_contents_preserved":True,
        "budget_months_verified":len(months),"pending_rows":after["pending_rows"],"pending_cents":after["pending_cents"],
        "independent_backup_deferred":True,"unknown_data_preserved":True}
