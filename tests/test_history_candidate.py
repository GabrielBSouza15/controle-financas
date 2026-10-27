import json
import re
from hashlib import sha256
from copy import deepcopy
import pytest
from sqlalchemy import select,func
from sqlalchemy.exc import IntegrityError
from fastapi.testclient import TestClient
from test_settlements import client, entry, ORIGIN
from app.main import create_app
from app.persistence.database import transaction
from app.persistence.models import HistoryBatch,HistoryRecord,Entry,Obligation,FinancialEvent
from app.services.history_candidate import import_history,summary,records
from app.services.catalogs import ConflictError
from app.persistence.entries import PersistenceValidationError
from app.services.backup import create_backup,restore_backup,database_fingerprint

def documents(change=None):
    def row(number,amount,kind="expense",method="credit",paid=True,person="self"):
        return {"row_number":number,"role":"observed_obligation","status":"prepared","blockers":[],
            "proposal":{"description":"Ficticio","kind":kind,"amount_cents":amount,"reference_on":"2025-01-03",
            "observed_number":10 if number==3 else 1,"observed_count":12 if number==3 else 1,
            "responsible_code":person,"payment_method":method,"card_code":"nubank" if method=="credit" else None,
            "simulated_cycle":"2025-01-01" if method=="credit" else None,"category_original":None,"paid_evidence":paid}}
    rows=[row(3,5000),row(4,7000,paid=False,person="parents"),
          row(5,30000,kind="income",method="pix"),row(6,42000,kind="income",method="pix"),row(7,100)]
    rows[3]["role"]="opening_balance"
    rows[3]["opening_balance"]={"confirmed_by_user":True,"amount_cents":42000,"as_of":"2025-01-03","bank_code":"nubank"}
    rows[4]["status"]="excluded"
    preview={"source_sha256":"a"*64,"rows":rows,"summary":{"payment_pending_cents":7000},"sequence_candidates":[]}
    if change:change(preview)
    content=json.dumps(preview).encode()
    confirm={"source_sha256":"a"*64,"preview_sha256":sha256(content).hexdigest(),
             "confirmed_groups":[{"id":"partial","source_rows":[3]}]}
    return content,json.dumps(confirm).encode()

def test_partial_numbers_and_payment_evidence_do_not_generate_cash(client):
    a,b=documents()
    saved=import_history(client.app.state.engine,a,b)
    assert saved["created"] and import_history(client.app.state.engine,a,b)=={"id":saved["id"],"created":False}
    s=summary(client.app.state.engine)
    assert s["source_rows"]==5 and s["observed_obligations"]==3 and s["excluded_rows"]==1
    assert s["pending_cents"]==7000 and s["invoices"][0]["documented_paid_undated_cents"]==5000
    assert s["months"][0]["self_income_cents"]==30000
    assert s["opening_balances"][0]["amount_cents"]==42000 and not s["absolute_bank_balance_available"]
    assert s["cash_in_cents"]==s["cash_out_cents"]==0
    first=records(client.app.state.engine)["items"][0]
    assert (first["observed_number"],first["observed_count"])==(10,12)
    assert first["paid_on"] is None and first["contract_total_cents"] is None
    with transaction(client.app.state.engine) as session:
        assert all(session.scalar(select(func.count()).select_from(model))==0 for model in (Entry,Obligation,FinancialEvent))

@pytest.mark.parametrize("change",[
    lambda d:d["rows"].append(deepcopy(d["rows"][0])),
    lambda d:d["rows"][0]["proposal"].update(amount_cents=True),
    lambda d:d["rows"][0]["proposal"].update(paid_evidence=1),
    lambda d:d["summary"].update(payment_pending_cents=1),
    lambda d:d["rows"][3]["opening_balance"].update(as_of="2024-01-01"),
])
def test_invalid_candidate_rolls_back(client,change):
    with pytest.raises(PersistenceValidationError):import_history(client.app.state.engine,*documents(change))
    with transaction(client.app.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(HistoryBatch))==0

def test_different_interpretation_conflicts_and_mixing_is_refused(client):
    import_history(client.app.state.engine,*documents())
    changed=documents(lambda d:d["rows"][0]["proposal"].update(amount_cents=5001))
    with pytest.raises(ConflictError):import_history(client.app.state.engine,*changed)

def test_operational_entry_prevents_candidate_import(client):
    entry(client)
    with pytest.raises(ConflictError):import_history(client.app.state.engine,*documents())

def test_sealed_history_is_immutable_in_sql(client):
    import_history(client.app.state.engine,*documents())
    for sql in ["DELETE FROM history_records","UPDATE history_records SET amount_cents=1",
                "DELETE FROM history_batches","UPDATE history_batches SET pending_cents=0",
                "INSERT INTO history_records SELECT id+100,batch_id,source_row+100,role,description,kind,amount_cents,reference_on,observed_number,observed_count,responsible_code,payment_method,card_code,simulated_cycle,category_original,paid_evidence,group_key,group_state,evidence_json FROM history_records WHERE id=1"]:
        with pytest.raises(IntegrityError):
            with client.app.state.engine.begin() as c:c.exec_driver_sql(sql)

def test_candidate_http_allows_integrated_commands_and_dashboard_history(client,tmp_path):
    import_history(client.app.state.engine,*documents())
    with TestClient(create_app(client.app.state.database_path,backup_root=tmp_path/"copies"),base_url=ORIGIN) as c:
        token=re.search(r'name="csrf-token" content="([^"]+)"',c.get("/").text).group(1)
        c.headers.update({"Origin":ORIGIN,"X-CSRF-Token":token})
        assert c.get("/api/history/summary").json()["pending_cents"]==7000
        response=c.get("/api/history/records",params={"pending_only":True,"limit":1})
        assert response.json()["total"]==1 and response.json()["items"][0]["parents_repayment_state"]=="unknown"
        assert c.get("/api/history/records/1").json()["evidence"]["row_number"]==3
        assert c.post("/api/categories",json={"name":"New category"}).status_code==201
        assert c.post("/api/maintenance/backups",json={"request_key":"12345678-1234-1234-1234-123456789abc"}).status_code==200
        assert c.get("/api/dashboard",params={"month":"2025-01","months":3}).json()["budget"]["total_self_expense_cents"]==5000

def test_candidate_backup_restore_keeps_unknowns_and_exact_totals(client,tmp_path):
    import_history(client.app.state.engine,*documents())
    before=summary(client.app.state.engine)
    copy=create_backup(client.app.state.database_path,tmp_path/"copies")
    path=tmp_path/"restored.db"
    restore_backup(copy["folder"],path)
    from app.persistence.database import make_engine
    engine=make_engine(path)
    try:
        assert summary(engine)==before
        assert database_fingerprint(path)==database_fingerprint(client.app.state.database_path)
    finally:engine.dispose()


def test_real_builder_serializes_civil_dates_and_is_idempotent(client,tmp_path,monkeypatch):
    import importlib.util
    from pathlib import Path
    from app.services import backup as backups
    script=Path(__file__).resolve().parents[1]/"scripts/build_history_candidate.py"
    spec=importlib.util.spec_from_file_location("history_builder_test",script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    root=tmp_path/"project";root.mkdir()
    source_bytes=b"fictional source bytes; never execute"
    source_hash=sha256(source_bytes).hexdigest()
    source=root/"imports"/source_hash/"observed-obligations-v1/source.xlsm"
    source.parent.mkdir(parents=True);source.write_bytes(source_bytes)
    a,b=documents();preview=json.loads(a);preview["source_sha256"]=source_hash
    a=json.dumps(preview).encode()
    confirmation=json.loads(b);confirmation.update(source_sha256=source_hash,preview_sha256=sha256(a).hexdigest())
    b=json.dumps(confirmation).encode()
    for relative,content in [("private/history-reconstruction-preview-v3/preview.json",a),
                             ("private/history-confirmations-20261019/confirmed-groups.json",b)]:
        path=root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
    monkeypatch.setattr(module,"PROJECT_ROOT",root)
    monkeypatch.setattr(module,"APPROVED_SOURCE_SHA256",source_hash)
    monkeypatch.setattr(module,"DEFAULT_DATABASE_PATH",client.app.state.database_path)
    monkeypatch.setattr(module,"create_backup",lambda source:backups.create_backup(source,root/"copies"))
    result=module.build(root/"history-candidate.db")
    report=json.loads(Path(result["validation"]).read_text())
    assert report["historical_summary"]["opening_balances"][0]["reference_on"]=="2025-01-03"
    assert report["verified_restoration"] and result["created"]
    assert not module.build(root/"history-candidate.db")["created"]
