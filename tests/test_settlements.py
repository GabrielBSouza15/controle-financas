from app.persistence.migrations import head_revision
"""Fatos confirmados, alocações e caixa sem duplicar receitas/despesas."""
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor
import re
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from app.main import create_app
from app.persistence.database import transaction
from app.persistence.migrations import upgrade_database
from app.persistence.models import (AllocationBatch, Base, Entry, EventReversal, FinancialEvent,
    Invoice, InvoiceClosure, Obligation, SettlementAllocation)
from app.services.settlements import today_local

ORIGIN="http://127.0.0.1:8000"

@pytest.fixture
def client(tmp_path):
    path=tmp_path/"settlements.db";upgrade_database(path)
    with TestClient(create_app(path),base_url=ORIGIN) as c:
        token=re.search(r'name="csrf-token" content="([^"]+)"',c.get("/").text).group(1)
        c.headers.update({"Origin":ORIGIN,"X-CSRF-Token":token})
        c.catalogs=c.post("/api/setup",json={"effective_from":"2020-01-01"}).json()
        c.account=c.post("/api/accounts",json={"name":"Conta fictícia"}).json()["id"]
        c.card=next(x["id"] for x in c.catalogs["cards"] if x["name"]=="Nubank")
        yield c

def entry(c,**changes):
    values=dict(request_key="entry-self",description="Despesa fictícia",occurred_on="2026-10-01",total_cents=150000,
        installment_count=1,responsible_id=1,payment_method="credit",kind="expense",card_id=c.card)
    response=c.post("/api/entries",json=values|changes);assert response.status_code==200,response.text
    return response.json()

def allocations(entry,amount=None):
    part=entry["obligations"][0]
    return [{"obligation_id":part["obligation_id"],"amount_cents":amount if amount is not None else part["amount_cents"]}]

def event(c,**changes):
    return dict(request_key="payment-self",kind="invoice_payment",occurred_on="2026-10-05",amount_cents=150000,
        payer_id=1,account_id=c.account,invoice_id=1,reason="Comprovante fictício",allocations=[])|changes

def post(c,data):
    response=c.post("/api/settlements",json=data);assert response.status_code==200,response.text
    return response.json()

def test_invoice_payment_and_partial_repayment_have_independent_effects(client):
    own=entry(client);parents=entry(client,request_key="entry-parents",responsible_id=2)
    payment=post(client,event(client,amount_cents=300000,allocations=allocations(own)+allocations(parents)))
    invoice=client.get("/api/invoices/1").json()
    assert invoice["total_cents"]==300000 and invoice["self_cents"]==150000
    assert invoice["issuer_paid_cents"]==300000 and invoice["liquidation_status"]=="paid" and invoice["status"]=="open"
    assert invoice["parents_uncovered_cents"]==150000
    repaid=post(client,event(client,request_key="repayment",kind="repayment",amount_cents=40000,payer_id=2,invoice_id=None,allocations=allocations(parents,40000)))
    updated=client.get("/api/invoices/1").json()
    assert updated["parents_uncovered_cents"]==110000 and updated["issuer_paid_cents"]==300000
    cash=client.get("/api/settlements").json()
    assert (cash["cash_in_cents"],cash["cash_out_cents"],cash["cash_variation_cents"])==(40000,300000,-260000)
    with transaction(client.app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(Entry))==2
    assert payment["kind"]=="invoice_payment" and repaid["kind"]=="repayment"

def test_parents_direct_payment_reduces_both_dimensions_without_own_cash(client):
    parent=entry(client,responsible_id=2)
    post(client,event(client,payer_id=2,account_id=None,amount_cents=40000,allocations=allocations(parent,40000)))
    invoice=client.get("/api/invoices/1").json()
    assert invoice["issuer_remaining_cents"]==110000 and invoice["parents_uncovered_cents"]==110000
    cash=client.get("/api/settlements").json();assert cash["cash_in_cents"]==cash["cash_out_cents"]==0
    assert cash["items"][0]["cash_direction"]=="none"

def test_advance_and_later_allocation_do_not_create_second_cash_entry(client):
    parent=entry(client,responsible_id=2)
    advance=post(client,event(client,kind="repayment",payer_id=2,invoice_id=None,amount_cents=100000,allocations=[]))
    assert advance["available_credit_cents"]==100000
    data={"request_key":"allocate-credit","reason":"Aplicação fictícia","allocations":allocations(parent,40000)}
    response=client.post(f'/api/settlements/{advance["id"]}/allocate',json=data)
    assert response.status_code==200,response.text
    assert response.json()["available_credit_cents"]==60000
    assert client.post(f'/api/settlements/{advance["id"]}/allocate',json=data).json()==response.json()
    cash=client.get("/api/settlements").json()
    assert cash["total"]==1 and cash["cash_in_cents"]==100000 and cash["available_credit_cents"]==60000
    assert client.get("/api/invoices/1").json()["parents_uncovered_cents"]==110000

def test_reversal_is_correction_not_new_cash_and_keeps_evidence(client):
    original=entry(client)
    payment=post(client,event(client,allocations=allocations(original)))
    data={"request_key":"reverse","reason":"Comprovante lançado por engano"}
    r=client.post(f'/api/settlements/{payment["id"]}/reverse',json=data)
    assert r.status_code==200 and r.json()["status"]=="reversed"
    assert client.post(f'/api/settlements/{payment["id"]}/reverse',json=data).json()==r.json()
    assert r.json()["allocations"]==payment["allocations"]
    assert client.get("/api/settlements").json()["total"]==0
    history=client.get("/api/settlements",params={"status":"all"}).json()
    assert history["total"]==1 and history["cash_out_cents"]==0
    assert client.get("/api/invoices/1").json()["issuer_paid_cents"]==0
    assert not client.get(f'/api/entries/{original["id"]}').json()["can_modify"]

def test_settled_entry_cannot_change_or_cancel_even_after_reversal(client):
    original=entry(client)
    payment=post(client,event(client,allocations=allocations(original)))
    for endpoint in ["cancel","edit"]:
        data={"request_key":endpoint,"reason":"Tentativa"}
        if endpoint=="edit":
            data.update(description="Alterada",occurred_on="2026-10-01",total_cents=200000,installment_count=1,responsible_id=1,payment_method="credit",kind="expense",card_id=client.card)
        assert client.post(f'/api/entries/{original["id"]}/{endpoint}',json=data).status_code==409
    client.post(f'/api/settlements/{payment["id"]}/reverse',json={"request_key":"reverse","reason":"Engano"})
    assert client.post(f'/api/entries/{original["id"]}/cancel',json={"request_key":"cancel-again","reason":"Teste"}).status_code==409

@pytest.mark.parametrize("kind,entry_kind,payer,account,direction",[("expense_payment","expense",1,True,"out"),("expense_payment","expense",2,False,"none"),("income_receipt","income",None,True,"in")])
def test_noncredit_settlements(client,kind,entry_kind,payer,account,direction):
    original=entry(client,kind=entry_kind,payment_method="pix",card_id=None,responsible_id=payer or 1)
    paid=post(client,event(client,kind=kind,payer_id=payer,account_id=client.account if account else None,invoice_id=None,allocations=allocations(original)))
    assert paid["cash_direction"]==direction
    assert client.get("/api/invoices").json()["total"]==0
    detail=client.get(f'/api/entries/{original["id"]}').json()
    assert detail["obligations"][0]["payment_cents"]==150000 and not detail["can_modify"]

@pytest.mark.parametrize("change",[{"amount_cents":True},{"amount_cents":1.5},{"amount_cents":"100"},{"amount_cents":-1},{"amount_cents":2**53},{"occurred_on":"9999-01-01"},{"occurred_on":"2026-10-05T00:00:00"},{"reason":" "},{"extra":"no"},{"account_id":None},{"payer_id":2},{"invoice_id":999}])
def test_invalid_events_have_no_cash_or_allocation(client,change):
    original=entry(client)
    result=client.post("/api/settlements",json=event(client,allocations=allocations(original))|change)
    assert result.status_code in {404,422},result.text
    with transaction(client.app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(FinancialEvent))==0
        assert s.scalar(select(func.count()).select_from(AllocationBatch))==0
        assert s.scalar(select(func.count()).select_from(SettlementAllocation))==0

def test_overpayment_and_double_parent_coverage_are_blocked(client):
    parent=entry(client,responsible_id=2)
    post(client,event(client,kind="repayment",payer_id=2,invoice_id=None,allocations=allocations(parent)))
    # Repasse ao titular já cobre a responsabilidade; pagamento direto do grupo não pode cobri-la outra vez.
    direct=client.post("/api/settlements",json=event(client,request_key="direct",payer_id=2,account_id=None,amount_cents=1,allocations=allocations(parent,1)))
    assert direct.status_code==422
    # O titular ainda precisa pagar ao banco: essa outra dimensão permanece livre.
    post(client,event(client,request_key="bank",allocations=allocations(parent)))
    assert client.post("/api/settlements",json=event(client,request_key="excess",amount_cents=1,allocations=allocations(parent,1))).status_code==422

def test_keys_repeat_without_duplicate_and_cannot_cross_operations(client):
    original=entry(client);data=event(client,allocations=allocations(original))
    first=post(client,data)
    assert post(client,data)==first
    assert client.post("/api/settlements",json=data|{"reason":"Diferente"}).status_code==409
    assert client.post("/api/settlements",json=data|{"request_key":"entry-self"}).status_code==409
    assert client.post(f'/api/entries/{original["id"]}/cancel',json={"request_key":data["request_key"],"reason":"Teste"}).status_code==409
    assert client.post(f'/api/settlements/{first["id"]}/reverse',json={"request_key":data["request_key"],"reason":"Teste"}).status_code==409

def test_invoice_closure_confirms_total_not_payment_and_is_idempotent(client):
    original=entry(client)
    payload={"request_key":"close","reason":"Conferida com emissor fictício","expected_total_cents":150000}
    assert client.post("/api/invoices/1/close",json=payload|{"expected_total_cents":1}).status_code==409
    r=client.post("/api/invoices/1/close",json=payload)
    assert r.status_code==200,r.text
    assert r.json()["status"]=="closed" and r.json()["issuer_paid_cents"]==0
    assert r.json()["closure"]["total_cents"]==150000
    assert client.post("/api/invoices/1/close",json=payload).json()==r.json()
    assert client.post("/api/invoices/1/close",json=payload|{"request_key":"another-close"}).status_code==409
    # Fatura fechada pode receber pagamento, sem mudar sua consolidação.
    post(client,event(client,allocations=allocations(original)))
    assert client.get("/api/invoices/1").json()["liquidation_status"]=="paid"

def test_reversed_advance_cannot_be_allocated_and_releases_coverage(client):
    original=entry(client,responsible_id=2)
    advance=post(client,event(client,kind="repayment",payer_id=2,invoice_id=None,amount_cents=150000,allocations=allocations(original)))
    client.post(f'/api/settlements/{advance["id"]}/reverse',json={"request_key":"reverse","reason":"Engano"})
    data={"request_key":"late","reason":"Teste","allocations":allocations(original,1)}
    assert client.post(f'/api/settlements/{advance["id"]}/allocate',json=data).status_code==404
    assert client.get("/api/invoices/1").json()["parents_uncovered_cents"]==150000

def test_available_obligations_respect_kind_payer_and_remaining(client):
    own=entry(client);parent=entry(client,request_key="parents",responsible_id=2)
    rows=client.get("/api/settlements/obligations",params={"kind":"invoice_payment","payer_id":2,"invoice_id":1}).json()
    assert rows["total"]==1 and rows["items"][0]["entry_id"]==parent["id"]
    post(client,event(client,allocations=allocations(own,40000),amount_cents=40000))
    rows=client.get("/api/settlements/obligations",params={"kind":"invoice_payment","payer_id":1,"invoice_id":1,"entry_id":own["id"]}).json()
    assert rows["items"][0]["available_cents"]==110000
    assert client.get("/api/settlements/obligations",params={"kind":"invoice_payment","payer_id":1}).status_code==422

def test_financial_history_and_settled_schedule_reject_sql_updates(client):
    original=entry(client);payment=post(client,event(client,allocations=allocations(original)))
    client.post(f'/api/settlements/{payment["id"]}/reverse',json={"request_key":"reverse","reason":"Teste"})
    for table in ["financial_events","allocation_batches","settlement_allocations","event_reversals"]:
        with pytest.raises(IntegrityError):
            with client.app.state.engine.begin() as conn: conn.exec_driver_sql(f"DELETE FROM {table}")
    for sql in ["UPDATE financial_events SET amount_cents=1","UPDATE allocation_batches SET posted=0","UPDATE settlement_allocations SET amount_cents=1","UPDATE entries SET description='changed'","DELETE FROM obligations"]:
        with pytest.raises(IntegrityError):
            with client.app.state.engine.begin() as conn: conn.exec_driver_sql(sql)


def test_duplicate_allocations_and_late_invalid_reference_roll_back_whole_event(client):
    own=entry(client)
    for rows in [allocations(own,1)*2,allocations(own,100)+[{"obligation_id":999,"amount_cents":100}]]:
        response=client.post("/api/settlements",json=event(client,amount_cents=sum(a["amount_cents"] for a in rows),allocations=rows))
        assert response.status_code in {409,422}
        assert client.get("/api/settlements").json()["total"]==0
        with transaction(client.app.state.engine) as s:
            assert s.scalar(select(func.count()).select_from(FinancialEvent))==0

def test_cancelled_and_wrong_invoice_obligations_cannot_be_paid(client):
    own=entry(client)
    other=entry(client,request_key="other-cycle",occurred_on="2026-10-05")
    assert client.post("/api/settlements",json=event(client,allocations=allocations(other))).status_code==422
    client.post(f'/api/entries/{own["id"]}/cancel',json={"request_key":"cancel","reason":"Erro"})
    assert client.post("/api/settlements",json=event(client,allocations=allocations(own))).status_code==409

def test_credit_before_purchase_and_multiple_later_batches(client):
    advance=post(client,event(client,request_key="advance",kind="repayment",payer_id=2,invoice_id=None,occurred_on="2026-09-01",amount_cents=100000,allocations=[]))
    parent=entry(client,responsible_id=2)
    for number in range(2):
        data={"request_key":f"batch-{number}","reason":"Aplicação parcial","allocations":allocations(parent,40000)}
        result=client.post(f'/api/settlements/{advance["id"]}/allocate',json=data)
        assert result.status_code==200,result.text
    assert result.json()["available_credit_cents"]==20000
    excess=client.post(f'/api/settlements/{advance["id"]}/allocate',json={"request_key":"excess","reason":"Excesso","allocations":allocations(parent,20001)})
    assert excess.status_code==422
    assert client.get(f'/api/settlements/{advance["id"]}').json()["available_credit_cents"]==20000
    assert client.get("/api/settlements").json()["cash_in_cents"]==100000

def test_direct_payment_cannot_be_for_self_or_mixed_with_own_account(client):
    own=entry(client)
    assert client.post("/api/settlements",json=event(client,payer_id=2,account_id=None,allocations=allocations(own))).status_code==422
    parent=entry(client,request_key="parents",responsible_id=2)
    assert client.post("/api/settlements",json=event(client,payer_id=2,account_id=client.account,allocations=allocations(parent))).status_code==422

def test_date_filters_pagination_and_correction_have_complete_cash_totals(client):
    parent=entry(client,responsible_id=2)
    for number,when in enumerate(["2026-10-02","2026-10-03","2026-10-04"]):
        post(client,event(client,request_key=f"part-{number}",kind="repayment",payer_id=2,invoice_id=None,occurred_on=when,amount_cents=10000,allocations=allocations(parent,10000)))
    result=client.get("/api/settlements",params={"limit":1,"offset":1,"date_from":"2026-10-02","date_to":"2026-10-04","account_id":client.account}).json()
    assert result["total"]==3 and len(result["items"])==1 and result["cash_in_cents"]==30000
    identity=result["items"][0]["id"]
    client.post(f"/api/settlements/{identity}/reverse",json={"request_key":"reverse","reason":"Engano"})
    history=client.get("/api/settlements",params={"status":"all"}).json()
    assert history["total"]==3 and history["cash_in_cents"]==20000
    assert client.get("/api/settlements",params={"status":"reversed"}).json()["cash_in_cents"]==0
    assert client.get("/api/settlements",params={"date_from":"2026-10-04","date_to":"2026-10-02"}).status_code==422
    detail=client.get(f"/api/settlements/{identity}",params={"limit":1,"offset":1}).json()
    assert detail["allocations"]==[] and detail["allocation_count"]==1

def test_unknown_references_receipts_and_payment_dates_are_validated(client):
    own=entry(client)
    assert client.get("/api/settlements/999").status_code==404
    assert client.get("/api/settlements/0").status_code==422
    assert client.post("/api/settlements",json=event(client,occurred_on="2026-09-30",allocations=allocations(own))).status_code==422
    assert client.post("/api/settlements",json=event(client,kind="repayment",payer_id=1,invoice_id=None,allocations=[])).status_code==422
    assert client.post("/api/settlements",json=event(client,account_id=999,allocations=allocations(own))).status_code==422

def test_operations_require_origin_token_and_user_text_is_json_only(client):
    own=entry(client)
    data=event(client,reason='<script>alert(1)</script>',allocations=allocations(own))
    assert client.post("/api/settlements",json=data,headers={"Origin":"https://outside.example"}).status_code==403
    assert client.post("/api/settlements",json=data,headers={"X-CSRF-Token":"bad"}).status_code==403
    registered=post(client,data)
    assert registered["reason"]==data["reason"] and data["reason"] not in client.get("/").text
    assert client.post(f'/api/settlements/{registered["id"]}/reverse',json={"request_key":"reverse","reason":"Teste"},headers={"X-CSRF-Token":"bad"}).status_code==403
    assert "innerHTML" not in client.get("/static/settlements.js").text

def test_two_competing_partial_payments_cannot_exceed_one_obligation(client):
    from app.services.settlements import record_event
    own=entry(client,total_cents=100)
    def attempt(number):
        data=event(client,request_key=f"parallel-{number}",amount_cents=100,allocations=allocations(own,100),occurred_on=date(2026,10,5))
        try:return record_event(client.app.state.engine,data)["id"]
        except Exception as exc:return type(exc).__name__
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,[1,2]))
    assert len([x for x in results if type(x)is int])==1
    assert client.get("/api/invoices/1").json()["issuer_paid_cents"]==100

def test_unposted_batch_cannot_move_cash_and_sealing_requires_complete_allocation(client):
    own=entry(client)
    from app.services.settlements import canonical, now_text
    with transaction(client.app.state.engine) as s:
        event_row=FinancialEvent(request_key="draft-only",kind="invoice_payment",occurred_on=date(2026,10,5),amount_cents=100,payer_id=1,account_id=client.account,invoice_id=1,reason="Rascunho",recorded_at=now_text(),payload_json="{}")
        s.add(event_row);s.flush()
        batch=AllocationBatch(request_key="draft-only",event_id=event_row.id,amount_cents=100,reason="Rascunho",recorded_at=now_text(),payload_json="{}")
        s.add(batch);s.flush();batch_id=batch.id
    assert client.get("/api/settlements").json()["total"]==0
    with pytest.raises(IntegrityError):
        with client.app.state.engine.begin() as conn:conn.exec_driver_sql("UPDATE allocation_batches SET posted=1 WHERE id=?",(batch_id,))
    assert client.get("/api/settlements").json()["cash_out_cents"]==0

def test_account_ownership_and_role_cannot_reinterpret_history(client):
    own=entry(client);post(client,event(client,allocations=allocations(own)))
    with pytest.raises(IntegrityError):
        with client.app.state.engine.begin() as conn:conn.exec_driver_sql("UPDATE accounts SET owner_id=2")
    with pytest.raises(IntegrityError):
        with client.app.state.engine.begin() as conn:conn.exec_driver_sql("DELETE FROM responsibles WHERE id=2");conn.exec_driver_sql("UPDATE responsibles SET code='parents' WHERE id=1")

def test_populated_previous_schema_upgrade_preserves_exact_records(tmp_path):
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from app.persistence.database import make_engine
    from app.persistence.migrations import migration_config,current_revision
    from app.persistence.entries import EntryDraft,record_entry_in_session
    from app.services.catalogs import initialize_catalogs
    path=tmp_path/"before-0004.db";engine=make_engine(path)
    with engine.begin() as conn:
        config=migration_config();config.attributes["connection"]=conn;command.upgrade(config,"0003_entry_changes")
    initialize_catalogs(engine,date(2020,1,1))
    # Registros concretos do esquema anterior, sem usar contratos novos.
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO entries(request_key,description,kind,total_cents,installment_count,occurred_on,payment_method,responsible_id) VALUES('old','Anterior','expense',100,1,'2026-10-01','pix',1)")
        conn.exec_driver_sql("INSERT INTO obligations(entry_id,number,amount_cents,scheduled_date) VALUES(1,1,100,'2026-10-01')")
        before=conn.exec_driver_sql("SELECT * FROM obligations").all()
    upgrade_database(path)
    assert current_revision(path)==head_revision()
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT * FROM obligations").all()==before
        assert compare_metadata(MigrationContext.configure(conn),Base.metadata)==[]
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all()==[]
    engine.dispose()


def test_downgrade_with_financial_history_is_blocked(client):
    from alembic import command
    from app.persistence.migrations import current_revision,migration_config
    own=entry(client);post(client,event(client,allocations=allocations(own)))
    with pytest.raises(RuntimeError,match="histórico financeiro"):
        with client.app.state.engine.begin() as conn:
            config=migration_config();config.attributes["connection"]=conn;command.downgrade(config,"0003_entry_changes")
    assert current_revision(client.app.state.engine.url.database)==head_revision()
    assert client.get("/api/settlements").json()["cash_out_cents"]==150000


def test_sealing_prepared_batches_rechecks_capacity(client):
    from app.services.settlements import now_text
    own=entry(client,total_cents=100)
    batches=[]
    with transaction(client.app.state.engine) as s:
        for n in range(2):
            row=FinancialEvent(request_key=f"prepared-{n}",kind="invoice_payment",occurred_on=date(2026,10,5),amount_cents=100,payer_id=1,account_id=client.account,invoice_id=1,reason="Preparado",recorded_at=now_text(),payload_json="{}")
            s.add(row);s.flush()
            batch=AllocationBatch(request_key=f"prepared-{n}",event_id=row.id,amount_cents=100,reason="Preparado",recorded_at=now_text(),payload_json="{}")
            s.add(batch);s.flush();batches.append(batch.id)
            s.add(SettlementAllocation(batch_id=batch.id,obligation_id=own["obligations"][0]["obligation_id"],amount_cents=100));s.flush()
    with client.app.state.engine.begin() as conn:conn.exec_driver_sql("UPDATE allocation_batches SET posted=1 WHERE id=?",(batches[0],))
    with pytest.raises(IntegrityError):
        with client.app.state.engine.begin() as conn:conn.exec_driver_sql("UPDATE allocation_batches SET posted=1 WHERE id=?",(batches[1],))
    assert client.get("/api/invoices/1").json()["issuer_paid_cents"]==100
    assert client.get("/api/settlements").json()["total"]==1


def test_aggregate_cash_limit_never_uses_float_or_returns_rounded_values(client):
    maximum=2**53-1
    for number,amount in enumerate([maximum,1]):
        post(client,event(client,request_key=f"large-{number}",kind="repayment",payer_id=2,invoice_id=None,amount_cents=amount,allocations=[]))
    response=client.get("/api/settlements")
    assert response.status_code==422 and "faixa exata" in response.text


def test_closure_snapshot_cannot_be_changed_or_deleted(client):
    own=entry(client)
    result=client.post("/api/invoices/1/close",json={"request_key":"close","reason":"Conferida","expected_total_cents":150000})
    assert result.status_code==200,result.text
    for sql in ["UPDATE invoice_closures SET total_cents=1","DELETE FROM invoice_closures"]:
        with pytest.raises(IntegrityError):
            with client.app.state.engine.begin() as conn:conn.exec_driver_sql(sql)
    assert client.get("/api/invoices/1").json()["total_cents"]==150000
