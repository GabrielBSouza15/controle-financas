from datetime import date
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
import json
import xml.etree.ElementTree as E
import pytest
from sqlalchemy import func,select
from sqlalchemy.exc import IntegrityError
from app.persistence.database import make_engine,transaction,DEFAULT_DATABASE_PATH
from app.persistence.migrations import upgrade_database
from app.persistence.models import LegacyImportBatch,LegacySourceRow,LegacyObligation,Entry,FinancialEvent
from app.services.legacy_import import run_trial,import_prepared,ensure_trial_database
from app.services.legacy_reader import (EXPECTED_HEADERS,NS,LegacyValidationError,read_workbook,prepare_rows,controls,
    arithmetic_formula,APPROVED_SOURCE_SHA256)
S=NS["s"];R=NS["r"];P="http://schemas.openxmlformats.org/package/2006/relationships"
def q(name):return "{"+S+"}"+name
def workbook_bytes(rows,*,date1904=False):
    book=E.Element(q("workbook"));E.SubElement(book,q("workbookPr"),date1904="1" if date1904 else "0")
    sheets=E.SubElement(book,q("sheets"));E.SubElement(sheets,q("sheet"),{"name":"Banco de dados","sheetId":"1","{"+R+"}id":"rId1"})
    rel=E.Element("{"+P+"}Relationships");E.SubElement(rel,"{"+P+"}Relationship",Id="rId1",Target="worksheets/sheet1.xml",Type=R+"/worksheet")
    root=E.Element(q("worksheet"));data=E.SubElement(root,q("sheetData"))
    for number,values in [(2,dict(zip("BCDEFGHIJKLMNO",EXPECTED_HEADERS))),*[(i+3,r) for i,r in enumerate(rows)]]:
        row=E.SubElement(data,q("row"),r=str(number))
        for col,value in values.items():
            if value is None:continue
            ref=f"{col}{number}"
            if number==2 or col not in {"B","D","E","G","L"}:
                cell=E.SubElement(row,q("c"),r=ref,t="inlineStr");E.SubElement(E.SubElement(cell,q("is")),q("t")).text=str(value)
            elif type(value) is bool:
                cell=E.SubElement(row,q("c"),r=ref,t="b");E.SubElement(cell,q("v")).text="1" if value else "0"
            elif isinstance(value,dict):
                cell=E.SubElement(row,q("c"),r=ref,t=value.get("type","n"))
                if "formula" in value:E.SubElement(cell,q("f"),value.get("attrs",{})).text=value["formula"]
                if value.get("value") is not None:E.SubElement(cell,q("v")).text=value["value"]
            else:
                cell=E.SubElement(row,q("c"),r=ref,t="n");E.SubElement(cell,q("v")).text=str(value)
    note=E.SubElement(data,q("row"),r="2000");cell=E.SubElement(note,q("c"),r="Q2000",t="inlineStr");E.SubElement(E.SubElement(cell,q("is")),q("t")).text="Nota documental ficticia; nao e caixa."
    parts=E.SubElement(root,q("tableParts"),count="1");E.SubElement(parts,q("tablePart"),{"{"+R+"}id":"table1"})
    sheetrels=E.Element("{"+P+"}Relationships");E.SubElement(sheetrels,"{"+P+"}Relationship",Id="table1",Target="../tables/table1.xml",Type=R+"/table")
    table=E.Element(q("table"),id="1",displayName="Fluxo_de_Caixa",name="Fluxo_de_Caixa",ref=f"B2:O{len(rows)+2}")
    columns=E.SubElement(table,q("tableColumns"),count="14")
    for i,name in enumerate(EXPECTED_HEADERS):E.SubElement(columns,q("tableColumn"),id=str(i+1),name=name)
    stream=BytesIO()
    with ZipFile(stream,"w") as z:
        for name,element in [("xl/workbook.xml",book),("xl/_rels/workbook.xml.rels",rel),("xl/worksheets/sheet1.xml",root),("xl/worksheets/_rels/sheet1.xml.rels",sheetrels),("xl/tables/table1.xml",table)]:
            z.writestr(name,E.tostring(element,encoding="utf-8",xml_declaration=True))
        z.writestr("xl/vbaProject.bin",b"DO NOT EXECUTE: synthetic macro bytes")
    return stream.getvalue()

def source_row(**kw):
    row=dict(B=(date(2026,10,1)-date(1899,12,30)).days,C=" Compra ficticia ",D=1,E=1,F="Categoria ficticia",G="100.00",H="Pessoal",I="Despesa",J="PIX",K="Nubank",L=True,M="nao importar",N="nao importar",O="nao importar")
    return row|kw

def source_file(tmp_path,rows):
    content=workbook_bytes(rows);path=tmp_path/"source.xlsm";path.write_bytes(content);return path,sha256(content).hexdigest()

def test_trial_preserves_source_copy_raw_fields_and_is_repeatable(tmp_path):
    source,hash_=source_file(tmp_path,[source_row(),source_row(C=None,F=None,G="25.01",H="Pai/Mãe")])
    original=source.read_bytes();mtime=source.stat().st_mtime_ns
    result=run_trial(source,tmp_path/"trial.db",tmp_path/"imports",expected_sha256=hash_)
    repeated=run_trial(source,tmp_path/"trial.db",tmp_path/"imports",expected_sha256=hash_)
    assert result["created"] and not repeated["created"] and result["batch_id"]==repeated["batch_id"]
    assert source.read_bytes()==original and source.stat().st_mtime_ns==mtime
    folder=Path(result["report"]).parent
    assert (folder/"source.xlsm").read_bytes()==original
    extraction=json.loads((folder/"extraction.json").read_text(encoding="utf-8"))
    assert "Q2000" in extraction["context"]["outside_table"]["Banco de dados"]
    assert result["control"]["accepted_cents"]==12501 and result["control"]["reconciled"]
    engine=make_engine(tmp_path/"trial.db")
    with transaction(engine) as s:
        assert s.scalar(select(func.count()).select_from(LegacySourceRow))==2
        assert s.scalar(select(func.count()).select_from(LegacyObligation))==2
        assert s.scalar(select(func.count()).select_from(Entry))==0
        assert s.scalar(select(func.count()).select_from(FinancialEvent))==0
        row=s.scalar(select(LegacyObligation).where(LegacyObligation.description.is_(None)))
        assert row.amount_cents==2501 and row.responsible_code=="parents" and row.paid_evidence
    engine.dispose()

def test_exact_formulas_fractional_cents_and_binary_tails_remain_distinct(tmp_path):
    source,hash_=source_file(tmp_path,[
        source_row(G={"formula":"100/4","value":"25"}),
        source_row(G={"formula":"122.25/2","value":"61.125"}),
        source_row(G="48.129999999999997"),
        source_row(G={"formula":"144.39/3","value":"48.129999999999997"}),
        source_row(G={"formula":"SUM(A1)","value":"100"}),
    ])
    result=run_trial(source,tmp_path/"trial.db",tmp_path/"imports",expected_sha256=hash_)
    assert result["control"]["counts"]=={"accepted":2,"pending":3,"excluded":0}
    assert result["control"]["accepted_cents"]==7313
    assert result["control"]["issues"]["fractional_cent"]==1
    assert result["control"]["issues"]["binary_representation_review"]==1
    assert result["control"]["issues"]["unverified_value_formula"]==1
    assert Decimal(result["control"]["difference_decimal"])==0
    assert result["control"]["reconciled"]

def test_known_duplicate_exclusion_requires_approved_identity_and_content():
    data=workbook_bytes([source_row() for _ in range(547)])
    w=read_workbook(data)
    unapproved=prepare_rows(w);assert all(row["status"]=="accepted" for row in unapproved)
    w["sha256"]=APPROVED_SOURCE_SHA256
    approved=prepare_rows(w);by_number={row["row_number"]:row for row in approved}
    assert by_number[125]["status"]=="accepted" and by_number[129]["status"]=="excluded"
    assert len(approved)==547 and controls(approved)["counts"]["accepted"]==546
    for number in (174,236,345):assert "D13_independent_observation" in by_number[number]["issues"]
    w["rows"][129-3]["cells"]["C"]["value"]="Outra informacao"
    with pytest.raises(LegacyValidationError,match="D12"):prepare_rows(w)

def test_no_missing_installments_are_generated(tmp_path):
    source,hash_=source_file(tmp_path,[source_row(D=4,E=12,G="50.00"),source_row(D=1,E=3,G="48.13"),source_row(D=1,E=3,G="48.13")])
    result=run_trial(source,tmp_path/"trial.db",tmp_path/"imports",expected_sha256=hash_)
    engine=make_engine(tmp_path/"trial.db")
    with transaction(engine) as s:
        rows=list(s.scalars(select(LegacyObligation).order_by(LegacyObligation.id)))
        assert len(rows)==3 and [r.observed_number for r in rows]==[4,1,1]
        assert [r.observed_count for r in rows]==[12,3,3]
    engine.dispose()

def test_unknown_opening_balance_and_noncredit_paid_do_not_create_cash(tmp_path):
    source,hash_=source_file(tmp_path,[source_row(C="Conta Banco",I="Receita"),source_row(K="PIX",H="Pai/Mãe")])
    result=run_trial(source,tmp_path/"trial.db",tmp_path/"imports",expected_sha256=hash_)
    assert result["control"]["counts"]["pending"]==1 and result["control"]["counts"]["accepted"]==1
    assert result["control"]["issues"]["income_or_opening_balance"]==1
    assert result["control"]["issues"]["institution_unknown"]==1

@pytest.mark.parametrize("changes",[
    {"B":60},{"B":"1.5"},{"G":"-1"},{"G":None},{"H":"Pessoa desconhecida"},{"J":"Outro"},{"J":"Crédito","K":"Banco desconhecido"},{"D":4,"E":3},
])
def test_invalid_rows_are_preserved_pending_not_silently_dropped(changes):
    rows=prepare_rows(read_workbook(workbook_bytes([source_row(**changes)])))
    assert len(rows)==1 and rows[0]["status"]=="pending" and rows[0]["raw"]["row_number"]==3

def test_1904_date_epoch_and_phantom_leap_day_are_explicit():
    w=read_workbook(workbook_bytes([source_row(B=1)],date1904=True))
    assert prepare_rows(w)[0]["proposal"]["reference_on"]=="1904-01-02"
    w=read_workbook(workbook_bytes([source_row(B=60)]))
    assert "invalid_reference_date" in prepare_rows(w)[0]["blockers"]

def test_wrong_hash_and_personal_or_operational_database_are_rejected(tmp_path):
    source,hash_=source_file(tmp_path,[source_row()])
    with pytest.raises(LegacyValidationError,match="hash"):
        run_trial(source,tmp_path/"trial.db",tmp_path/"imports",expected_sha256="0"*64)
    assert not (tmp_path/"trial.db").exists()
    with pytest.raises(LegacyValidationError,match="pessoal"):ensure_trial_database(DEFAULT_DATABASE_PATH)
    path=tmp_path/"operational-trial.db";upgrade_database(path);engine=make_engine(path)
    with engine.begin() as conn:conn.exec_driver_sql("INSERT INTO responsibles(code,name) VALUES('self','Eu')")
    engine.dispose()
    with pytest.raises(LegacyValidationError,match="operacionais"):ensure_trial_database(path)

def test_failed_mapping_rolls_back_all_batch_records(tmp_path,monkeypatch):
    from app.services import legacy_import as module
    w=read_workbook(workbook_bytes([source_row(),source_row()]));rows=prepare_rows(w)
    path=tmp_path/"rollback-trial.db";upgrade_database(path);engine=make_engine(path)
    original=module.LegacyObligation;counter=0
    def fail_second(**data):
        nonlocal counter
        counter+=1
        if counter==2:raise RuntimeError("Falha de teste")
        return original(**data)
    monkeypatch.setattr(module,"LegacyObligation",fail_second)
    with pytest.raises(RuntimeError):import_prepared(engine,w,rows,"fake.xlsm")
    with transaction(engine) as s:
        for model in (LegacyImportBatch,LegacySourceRow,original):assert s.scalar(select(func.count()).select_from(model))==0
    engine.dispose()

def test_evidence_cannot_be_rewritten_by_sql(tmp_path):
    source,hash_=source_file(tmp_path,[source_row()])
    run_trial(source,tmp_path/"trial.db",tmp_path/"imports",expected_sha256=hash_)
    engine=make_engine(tmp_path/"trial.db")
    for sql in ["UPDATE legacy_source_rows SET status='excluded'","DELETE FROM legacy_import_batches","UPDATE legacy_obligations SET amount_cents=1"]:
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:conn.exec_driver_sql(sql)
    engine.dispose()

@pytest.mark.parametrize("expression",["SUM(A1)","__import__('os').getcwd()","True","1/0","2**100","[1][0]"])
def test_formula_reader_does_not_execute_python_or_external_references(expression):
    with pytest.raises(LegacyValidationError):arithmetic_formula(expression)
