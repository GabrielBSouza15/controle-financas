"""Correções históricas, caixa e recorrências exercitados só em bancos fictícios."""
from datetime import date
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select,func
from sqlalchemy.exc import IntegrityError
from test_settlements import client,ORIGIN
from test_history_candidate import documents
from test_history_events import prepare,payment
from app.persistence.database import transaction
from app.persistence.models import HistoryOperation,HistoryRecord
from app.services.history_candidate import import_history

def op(c,kind,**values):
    data={"request_key":"op-"+kind,"kind":kind,"record_id":2,"amount_cents":1000,"occurred_on":"2026-10-01","reason":"Teste ficticio"}
    return c.post("/api/history/operations",json=data|values)

def row(c,id=2):return c.get(f"/api/history/records/{id}").json()
def dash(c):return c.get("/api/dashboard?month=2025-01&months=3").json()
def link(c):return c.get("/api/history/operations").json()["links"][0]

def test_adjustment_changes_budget_but_never_source_or_cash(client):
    prepare(client)
    response=op(client,"adjustment",amount_cents=-2000);assert response.status_code==200,response.text
    assert op(client,"adjustment",amount_cents=-2000).json()==response.json()
    assert op(client,"adjustment",amount_cents=-2001).status_code==409
    assert row(client)["amount_cents"]==7000 and row(client)["effective_amount_cents"]==5000
    assert row(client)["pending_cents"]==5000 and dash(client)["budget"]["parents_expense_cents"]==5000
    assert dash(client)["budget"]["cash_out_cents"]==0
    assert op(client,"adjustment",request_key="too-far",amount_cents=-5001).status_code==409
    with pytest.raises(IntegrityError),transaction(client.app.state.engine) as s:
        s.connection().exec_driver_sql("DELETE FROM history_operations")

def test_paid_baseline_refund_records_actual_cash_without_fabricating_original_date(client):
    prepare(client)
    assert op(client,"adjustment",record_id=1,amount_cents=-2000).status_code==200
    r=op(client,"baseline_refund",record_id=1,amount_cents=2000,account_id=client.account)
    assert r.status_code==200,r.text
    assert row(client,1)["paid_evidence"] and row(client,1)["paid_on"] is None
    assert row(client,1)["effective_amount_cents"]==3000 and row(client,1)["pending_cents"]==0
    cash=client.get("/api/dashboard?month=2026-10&months=3").json()["budget"]
    assert cash["cash_in_cents"]==2000 and cash["cash_out_cents"]==0
    assert dash(client)["budget"]["total_self_expense_cents"]==3000
    assert op(client,"baseline_refund",request_key="exceed",record_id=1,amount_cents=3001,account_id=client.account).status_code==409
    assert op(client,"baseline_refund",request_key="not-paid",amount_cents=1000,account_id=client.account).status_code==409

def test_release_does_not_erase_bank_payment_and_reallocation_does_not_duplicate_cash(client):
    prepare(client);assert payment(client,amount_cents=7000,allocations=[{"record_id":2,"amount_cents":7000}]).status_code==200
    l=link(client)
    r=op(client,"release",event_id=l["event_id"],allocation_id=l["allocation_id"],amount_cents=7000)
    assert r.status_code==200,r.text
    assert row(client)["pending_cents"]==7000
    assert payment(client,request_key="cannot-pay-twice").status_code==409
    r=op(client,"allocate",event_id=l["event_id"],amount_cents=7000)
    assert r.status_code==200,r.text
    assert row(client)["pending_cents"]==0
    info=client.get("/api/history/events/1").json()
    assert info["balance"]["available_cents"]==0 and info["balance"]["effective_cents"]==7000
    cash=client.get("/api/dashboard?month=2026-10&months=3").json()["budget"]
    assert cash["cash_out_cents"]==7000 and cash["cash_in_cents"]==0
    assert client.post("/api/history/events/1/reverse",json={"request_key":"cannot-annul","reason":"Preservar correcoes"}).status_code==409

def test_refund_reduces_link_and_net_bank_receipt_not_budget(client):
    prepare(client);assert payment(client).status_code==200
    l=link(client)
    r=op(client,"refund",event_id=l["event_id"],allocation_id=l["allocation_id"],account_id=client.account,amount_cents=1000)
    assert r.status_code==200,r.text
    assert row(client)["new_payment_cents"]==2000 and row(client)["pending_cents"]==5000
    assert dash(client)["budget"]["parents_expense_cents"]==7000
    cash=client.get("/api/dashboard?month=2026-10&months=3").json()["budget"]
    assert (cash["cash_out_cents"],cash["cash_in_cents"])==(3000,1000)
    assert op(client,"refund",request_key="over-refund",event_id=1,allocation_id=l["allocation_id"],account_id=client.account,amount_cents=2001).status_code==409

def test_unallocated_refund_and_extra_link_can_be_corrected(client):
    prepare(client);assert payment(client).status_code==200
    l=link(client)
    assert op(client,"release",event_id=1,allocation_id=l["allocation_id"],amount_cents=3000).status_code==200
    response=op(client,"refund_credit",record_id=None,event_id=1,account_id=client.account,amount_cents=1000)
    assert response.status_code==200,response.text
    new=op(client,"allocate",event_id=1,amount_cents=2000);assert new.status_code==200,new.text
    released=op(client,"release",request_key="extra-release",event_id=1,source_operation_id=new.json()["id"],amount_cents=1000)
    assert released.status_code==200,released.text
    assert op(client,"refund_credit",request_key="extra-refund",record_id=None,event_id=1,account_id=client.account,amount_cents=1000).status_code==200
    assert client.get("/api/history/events/1").json()["balance"]["effective_cents"]==1000

def test_direct_parent_refund_has_no_own_cash_and_repayment_stays_separate(client):
    prepare(client);assert payment(client,payer_code="parents",account_id=None).status_code==200
    l=link(client)
    response=op(client,"refund",event_id=1,allocation_id=l["allocation_id"],account_id=None)
    assert response.status_code==200,response.text
    assert row(client)["new_payment_cents"]==row(client)["known_parents_covered_cents"]==2000
    cash=client.get("/api/dashboard?month=2026-10&months=3").json()["budget"]
    assert cash["cash_in_cents"]==cash["cash_out_cents"]==0

def test_cycle_move_is_audited_and_prohibited_after_link_or_paid_evidence(client):
    prepare(client)
    result=op(client,"move",amount_cents=0,target_month="2025-02")
    assert result.status_code==200,result.text
    assert row(client)["simulated_cycle"]=="2025-01-01" and row(client)["current_cycle"]=="2025-02-01"
    assert dash(client)["budget"]["parents_expense_cents"]==0
    assert client.get("/api/history/records?month=2025-02").json()["total"]==1
    assert payment(client).status_code==200
    assert op(client,"move",request_key="move-again",amount_cents=0,target_month="2025-03").status_code==409
    assert op(client,"move",request_key="move-paid",record_id=1,amount_cents=0,target_month="2025-02").status_code==409

def test_reconciliation_requires_existing_cycle_and_detects_later_change(client):
    prepare(client)
    r=op(client,"reconcile",record_id=None,card_code="nubank",target_month="2025-01",amount_cents=12000)
    assert r.status_code==200,r.text
    assert dash(client)["historical_invoices"][0]["reconciliation"]["state"]=="matched"
    assert op(client,"adjustment",amount_cents=-1000).status_code==200
    assert dash(client)["historical_invoices"][0]["reconciliation"]["state"]=="stale"
    assert op(client,"reconcile",request_key="wrong-total",record_id=None,card_code="nubank",target_month="2025-01",amount_cents=9999).status_code==200
    assert dash(client)["historical_invoices"][0]["reconciliation"]["state"]=="difference"

@pytest.mark.parametrize("changes",[
    {"amount_cents":True},{"amount_cents":1.1},{"occurred_on":"2099-01-01"},
    {"record_id":4},{"record_id":5},{"record_id":999},
    {"amount_cents":0},{"event_id":1},{"target_month":"2026-10"},
])
def test_invalid_adjustments_are_atomic(client,changes):
    prepare(client);response=op(client,"adjustment",**changes)
    assert response.status_code in {404,409,422},response.text
    with transaction(client.app.state.engine) as s:assert s.scalar(select(func.count()).select_from(HistoryOperation))==0

def test_recurring_and_standard_adjustments_are_unblocked_on_historical_base(client,tmp_path):
    import re
    from fastapi.testclient import TestClient
    from app.main import create_app
    from test_recurrences import series,decide
    from test_settlements import entry
    prepare(client)
    with TestClient(create_app(client.app.state.database_path),base_url=ORIGIN) as c:
        token=re.search(r'name="csrf-token" content="([^"]+)"',c.get("/").text).group(1)
        c.headers.update({"Origin":ORIGIN,"X-CSRF-Token":token});c.account=client.account;c.card=client.card
        r=series(c)
        assert decide(c,r["id"]).status_code==200
        new=entry(c,total_cents=2500,request_key="normal-adjustable")
        result=c.post("/api/adjustments/obligations",json={"request_key":"normal-delta","reason":"Teste","obligation_id":new["obligations"][0]["obligation_id"],"delta_cents":-500,"occurred_on":"2026-10-01"})
        assert result.status_code==200,result.text

def test_recurrence_overlap_confirmation_is_explicit_and_idempotent(client):
    from test_recurrences import series,decide
    prepare(client)
    s=series(client,description="Ficticio",amount_cents=5000,start_on="2025-01-03",payment_method="credit",card_id=client.card,account_id=None)
    kwargs={"month":"2025-01","expected_cents":5000,"actual_cents":5000,"occurred_on":"2025-01-03"}
    assert decide(client,s["id"],**kwargs).status_code==409
    result=decide(client,s["id"],**kwargs,history_confirmation={"record_ids":[1],"reason":"Compra mensal distinta conferida"})
    assert result.status_code==200,result.text
    assert decide(client,s["id"],**kwargs,history_confirmation={"record_ids":[1],"reason":"Compra mensal distinta conferida"}).json()==result.json()

def test_sql_cannot_pay_bank_again_after_release(client):
    from app.persistence.models import HistoryEvent,HistoryAllocation
    prepare(client);assert payment(client,amount_cents=7000,allocations=[{"record_id":2,"amount_cents":7000}]).status_code==200
    l=link(client)
    assert op(client,"release",event_id=1,allocation_id=l["allocation_id"],amount_cents=7000).status_code==200
    with pytest.raises(IntegrityError),transaction(client.app.state.engine) as s:
        e=HistoryEvent(request_key="sql-duplicate-bank",kind="invoice_payment",occurred_on=date(2026,10,1),
            amount_cents=7000,payer_code="self",account_id=client.account,reason="Teste SQL",recorded_at="2026-10-01T00:00:00+00:00",payload_json="{}",posted=False)
        s.add(e);s.flush();s.add(HistoryAllocation(event_id=e.id,record_id=2,amount_cents=7000));s.flush()
        e.posted=True;s.flush()
    assert client.get("/api/history/events").json()["total"]==1

def test_concurrent_refunds_never_exceed_original_link(client):
    prepare(client);assert payment(client).status_code==200
    l=link(client)
    def run(key):
        return op(client,"refund",request_key=key,event_id=1,allocation_id=l["allocation_id"],account_id=client.account,amount_cents=2000).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(run,["refund-a","refund-b"]))
    assert statuses.count(200)==1
    assert row(client)["new_payment_cents"]==1000
