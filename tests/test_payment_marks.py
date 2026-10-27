"""Quitação informada no cadastro, sem inventar conta ou movimento bancário."""
import re
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from test_web import client, ready, payload, ORIGIN
from app.main import create_app
from app.persistence.database import transaction
from app.persistence.models import EntryPaymentMark, Entry, FinancialEvent, Account, EntryChange, AllocationBatch, SettlementAllocation


def draft(c, **changes):
    return payload(c,payment_method="pix_debit",card_id=None,paid_without_account=True,
                   total_cents=10001,installment_count=3,occurred_on="2026-10-01")|changes


def test_paid_mark_persists_exact_installments_without_cash_or_accounts(ready):
    body=draft(ready)
    response=ready.post("/api/entries",json=body)
    assert response.status_code==200,response.text
    data=response.json()
    assert data["paid_without_account"] and not data["can_modify"]
    assert [p["payment_cents"] for p in data["obligations"]]==[3334,3334,3333]
    assert ready.post("/api/entries",json=body).json()==data
    result=ready.get("/api/personal/flow?date_from=2026-10-01&date_to=2026-12-31").json()
    assert len(result["items"])==3
    assert all(p["payment_status"]=="Pago" and p["pending_cents"]==0 for p in result["items"])
    assert sum(p["amount_cents"] for p in result["items"])==10001
    budget=ready.get("/api/dashboard?month=2026-10&months=3").json()["budget"]
    assert budget["total_self_expense_cents"]==3334
    assert budget["cash_out_cents"]==0
    with transaction(ready.app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(EntryPaymentMark))==1
        assert s.scalar(select(func.count()).select_from(Account))==0
        assert s.scalar(select(func.count()).select_from(FinancialEvent))==0
    with TestClient(create_app(ready.app.state.database_path),base_url=ORIGIN) as c:
        assert c.get(f'/api/entries/{data["id"]}').json()==data


@pytest.mark.parametrize("changes",[
    {"kind":"income"}, {"responsible_id":2,"parents_repayment":"received"},
    {"payment_method":"credit","card_id":1}, {"occurred_on":"2099-01-01"},
    {"account_id":1}, {"immediate_account_id":1}, {"paid_without_account":1},
])
def test_ineligible_or_future_marks_roll_back_entry(ready,changes):
    # Conta válida no caso que deve rejeitar dois modos de quitação simultâneos.
    if "account_id" in changes or "immediate_account_id" in changes:
        assert ready.post("/api/accounts",json={"name":"Conta fictícia"}).status_code==201
    r=ready.post("/api/entries",json=draft(ready,**changes))
    assert r.status_code==422,r.text
    assert ready.get("/api/entries").json()["total"]==0
    with transaction(ready.app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(EntryPaymentMark))==0
        assert s.scalar(select(func.count()).select_from(FinancialEvent))==0


def test_replay_cannot_add_or_remove_payment_mark(ready):
    body=draft(ready)
    assert ready.post("/api/entries",json=body).status_code==200
    assert ready.post("/api/entries",json=body|{"paid_without_account":False}).status_code==409
    plain=body|{"request_key":"plain-entry","paid_without_account":False}
    assert ready.post("/api/entries",json=plain).status_code==200
    assert ready.post("/api/entries",json=plain|{"paid_without_account":True}).status_code==409


def test_mark_prevents_second_payment_and_rewriting(ready):
    data=ready.post("/api/entries",json=draft(ready)).json()
    account=ready.post("/api/accounts",json={"name":"Conta fictícia"}).json()["id"]
    part=data["obligations"][0]
    payment=dict(request_key="double-payment",kind="expense_payment",occurred_on="2026-10-05",
        amount_cents=part["amount_cents"],payer_id=1,account_id=account,reason="Teste fictício",
        allocations=[dict(obligation_id=part["obligation_id"],amount_cents=part["amount_cents"])])
    assert ready.post("/api/settlements",json=payment).status_code==422
    assert ready.post(f'/api/entries/{data["id"]}/cancel',json=dict(request_key="cancel",reason="Teste")).status_code==409
    assert ready.post(f'/api/entries/{data["id"]}/edit',json=draft(ready,request_key="edit",reason="Teste")).status_code==422
    assert ready.get("/api/settlements").json()["total"]==0


def test_marks_are_immutable_and_sql_rejects_rewriting_or_wrong_kind(ready):
    data=ready.post("/api/entries",json=draft(ready)).json()
    for command in ("UPDATE entry_payment_marks SET recorded_at='changed'", "DELETE FROM entry_payment_marks"):
        with pytest.raises(IntegrityError):
            with ready.app.state.engine.begin() as c:
                c.exec_driver_sql(command)
    with pytest.raises(IntegrityError):
        with transaction(ready.app.state.engine) as s:
            s.add(EntryChange(request_key="sql-cancel",source_entry_id=data["id"],action="cancel",
                reason="Teste",recorded_at="2026-10-25",payload_json="{}"))
    income=ready.post("/api/entries",json=draft(ready,request_key="income",kind="income",paid_without_account=False)).json()
    with pytest.raises(IntegrityError):
        with transaction(ready.app.state.engine) as s:
            s.add(EntryPaymentMark(entry_id=income["id"],recorded_at="2026-10-25"))


def test_sql_cannot_allocate_another_payment_to_marked_expense(ready):
    from datetime import date
    data=ready.post("/api/entries",json=draft(ready)).json()
    account=ready.post("/api/accounts",json={"name":"Conta fictícia"}).json()["id"]
    with pytest.raises(IntegrityError,match="already marked paid"):
        with transaction(ready.app.state.engine) as s:
            event=FinancialEvent(request_key="sql-pay",kind="expense_payment",occurred_on=date(2026,10,5),
                amount_cents=3334,payer_id=1,account_id=account,reason="Teste",recorded_at="2026-10-25",payload_json="{}")
            s.add(event);s.flush()
            batch=AllocationBatch(request_key="sql-pay",event_id=event.id,amount_cents=3334,
                reason="Teste",recorded_at="2026-10-25",payload_json="{}")
            s.add(batch);s.flush()
            s.add(SettlementAllocation(batch_id=batch.id,obligation_id=data["obligations"][0]["obligation_id"],amount_cents=3334))


def test_marked_creation_after_startup_with_imported_history(ready):
    from test_history_integration import load
    load(ready)
    with TestClient(create_app(ready.app.state.database_path),base_url=ORIGIN) as c:
        assert c.app.state.history_candidate
        token=re.search(r'name="csrf-token" content="([^"]+)"',c.get("/").text).group(1)
        c.headers.update({"Origin":ORIGIN,"X-CSRF-Token":token})
        history=c.get("/api/history/summary").json()
        r=c.post("/api/entries",json=draft(ready))
        assert r.status_code==200,r.text
        assert r.json()["paid_without_account"]
        assert c.get("/api/history/summary").json()==history
        assert c.get("/api/catalogs").json()["accounts"]==[]
