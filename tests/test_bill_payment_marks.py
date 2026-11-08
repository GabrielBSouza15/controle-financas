from datetime import date
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from app.persistence.database import transaction
from app.persistence.models import BillPaymentMark
from test_settlements import client, entry, event, allocations, post as post_event
from test_personal import bill, post
from test_history_integration import load


def without_account(c, month="2026-10", total=3000, **changes):
    data = bill(c,month,total)
    data.pop("account_id")
    return dict(data,paid_without_account=True,**changes)


def test_whole_bill_without_account_replay_and_independent_dimensions(client):
    entry(client,total_cents=1000)
    parent=entry(client,total_cents=2000,request_key="parent",responsible_id=2)
    data=without_account(client)
    assert post(client,"bill",data)==dict(paid_cents=3000)
    assert post(client,"bill",data)==dict(paid_cents=3000)
    assert client.post("/api/personal/bill",json=dict(data,request_key="duplicate")).status_code==409
    assert client.post("/api/personal/bill",json=dict(data,occurred_on="2026-10-06")).status_code==409
    inv=client.get("/api/invoices/1").json()
    assert inv["issuer_remaining_cents"]==0 and inv["total_cents"]==3000
    assert inv["parents_uncovered_cents"]==2000
    assert client.get("/api/settlements").json()["cash_out_cents"]==0
    flow=client.get("/api/personal/flow?date_from=2026-10-01&date_to=2026-10-31").json()
    assert all(i["pending_cents"]==0 for i in flow["items"])
    # Repasse remains independently registrable after the bank is paid.
    post_event(client,event(client,request_key="repay",kind="repayment",payer_id=2,invoice_id=None,amount_cents=2000,allocations=allocations(parent,2000)))
    assert client.get("/api/invoices/1").json()["parents_uncovered_cents"]==0
    with transaction(client.app.state.engine) as s:
        marks=s.scalars(select(BillPaymentMark)).all()
        assert len(marks)==2 and all(m.occurred_on==date(2026,10,5) for m in marks)
        with pytest.raises(IntegrityError):
            s.execute(text("DELETE FROM bill_payment_marks"))


def test_mixed_history_and_new_no_account(client):
    load(client)
    entry(client,occurred_on="2025-01-04",total_cents=2500)
    data=without_account(client,"2025-01",9500)
    assert post(client,"bill",data)["paid_cents"]==9500
    assert post(client,"bill",data)["paid_cents"]==9500
    assert client.get("/api/history/summary").json()["pending_cents"]==0
    assert client.get("/api/personal/bill?card_id=1&month=2025-01").json()["remaining_cents"]==0
    assert client.get("/api/invoices/1").json()["issuer_remaining_cents"]==0
    assert client.get("/api/settlements").json()["cash_out_cents"]==0
    flow=client.get("/api/personal/flow?date_from=2025-01-01&date_to=2025-01-31").json()
    assert any(i["payment_status"]=="Pago sem data (planilha)" for i in flow["items"])
    assert all(i["pending_cents"]==0 for i in flow["items"] if i["kind"]=="expense")


def test_partial_with_account_then_remaining_without_account(client):
    own=entry(client,total_cents=3000)
    post_event(client,event(client,amount_cents=1000,allocations=allocations(own,1000)))
    assert post(client,"bill",without_account(client,total=2000))["paid_cents"]==2000
    inv=client.get("/api/invoices/1").json()
    assert inv["issuer_remaining_cents"]==0 and inv["issuer_paid_cents"]==3000
    assert client.get("/api/settlements").json()["cash_out_cents"]==1000
    assert client.post("/api/settlements",json=event(client,request_key="double",amount_cents=1,allocations=allocations(own,1))).status_code==422
    assert client.post(f"/api/entries/{own['id']}/cancel",json=dict(request_key="cancel",reason="Correção")).status_code==409


@pytest.mark.parametrize("changes", [dict(occurred_on="2099-01-01"),dict(occurred_on="2020-01-01"),dict(account_id=1),dict(paid_without_account=False),dict(paid_without_account=1)])
def test_invalid_attempt_has_no_effect(client,changes):
    entry(client,total_cents=3000)
    assert client.post("/api/personal/bill",json=dict(without_account(client),**changes)).status_code==422
    assert client.get("/api/invoices/1").json()["issuer_remaining_cents"]==3000
    with transaction(client.app.state.engine) as s:
        assert not s.scalars(select(BillPaymentMark)).all()


def test_empty_account_catalog_does_not_block_bill(client):
    entry(client,total_cents=3000)
    with transaction(client.app.state.engine) as s:
        s.execute(text("DELETE FROM accounts"))
    assert post(client,"bill",without_account(client))["paid_cents"]==3000
    html=client.get("/").text
    assert 'id="bill-payment-account"' not in html
    assert 'Não cria movimento em conta.' in html


def test_marked_history_allows_repayment_but_not_duplicate_payment(client):
    load(client)
    post(client,"bill",without_account(client,"2025-01",7000))
    # Existing historical evidence stays immutable; no second bank payment.
    from app.persistence.models import HistoryEvent
    with transaction(client.app.state.engine) as s:
        assert not s.scalars(select(HistoryEvent)).all()
        mark=s.scalar(select(BillPaymentMark).where(BillPaymentMark.record_id.is_not(None)))
        assert mark is not None
        with pytest.raises(IntegrityError):
            s.execute(text("UPDATE bill_payment_marks SET amount_cents=1 WHERE id=:id"),dict(id=mark.id))


def test_later_purchase_in_same_cycle_is_not_automatically_paid(client):
    entry(client,total_cents=3000)
    post(client,"bill",without_account(client))
    entry(client,request_key="later",total_cents=500)
    assert client.get("/api/personal/bill?card_id=1&month=2026-10").json()["remaining_cents"]==500
    post(client,"bill",dict(without_account(client,total=500),request_key="second-bill"))
    assert client.get("/api/invoices/1").json()["issuer_remaining_cents"]==0


def test_sql_rejects_mark_above_pending_capacity(client):
    from app.services.personal import save_operation
    own=entry(client,total_cents=3000)
    with pytest.raises(IntegrityError):
        with transaction(client.app.state.engine) as s:
            data=without_account(client,total=3001)
            data["month"]=date(2026,10,1)
            data["occurred_on"]=date(2026,10,5)
            save_operation(s,data,"bill_payment",dict(paid_cents=3001))
            s.add(BillPaymentMark(operation_key=data["request_key"],obligation_id=own["obligations"][0]["obligation_id"],amount_cents=3001,occurred_on=data["occurred_on"]))
