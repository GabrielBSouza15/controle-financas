"""Repasse como indicação orçamentária, sem liquidação ou caixa fictício."""
from datetime import date
import pytest
from sqlalchemy import select,func
from sqlalchemy.exc import IntegrityError
from test_settlements import client,entry
from test_recurrences import series,decide
from test_history_integration import load
from app.persistence.models import Entry,Obligation,Invoice,ParentBudgetChoice,FinancialEvent
from app.persistence.database import transaction

def payload(c,**changes):
    return dict(request_key="parent-budget",description="Compra fictícia dos pais",kind="expense",
        payment_method="pix_debit",responsible_id=2,total_cents=10000,installment_count=1,
        occurred_on="2025-12-15",parents_repayment="pending")|changes

def create(c,**changes):
    r=c.post("/api/entries",json=payload(c,**changes));assert r.status_code==200,r.text;return r.json()

def budget(c,month):
    r=c.get("/api/dashboard",params={"month":month,"months":3,"responsible_code":"parents"})
    assert r.status_code==200,r.text
    return r.json()["selected_budget"]

@pytest.mark.parametrize("status,expected",[("received","2025-12"),("pending","2026-01")])
def test_indication_uses_expense_month_not_computer_month_and_has_no_cash(client,status,expected):
    saved=create(client,parents_repayment=status)
    assert budget(client,expected)["total_expense_cents"]==10000
    other="2025-12" if status=="pending" else "2026-01"
    assert budget(client,other)["total_expense_cents"]==0
    part=saved["obligations"][0]
    assert part["scheduled_date"]=="2025-12-15"
    assert part["budget_month"]==expected+"-01"
    assert part["parents_covered_cents"]==part["payment_cents"]==0
    assert saved["parents_repayment"]==status
    with transaction(client.app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(FinancialEvent))==0
        assert s.scalar(select(func.count()).select_from(Invoice))==0

def test_installments_shift_exactly_once_and_keep_total_and_original_schedule(client):
    saved=create(client,total_cents=10000,installment_count=3,occurred_on="2024-12-31")
    assert [p["scheduled_date"] for p in saved["obligations"]]==["2024-12-31","2025-01-31","2025-02-28"]
    assert [p["budget_month"] for p in saved["obligations"]]==["2025-01-01","2025-02-01","2025-03-01"]
    assert [budget(client,m)["total_expense_cents"] for m in ["2025-01","2025-02","2025-03"]]==[3334,3333,3333]

def test_replay_or_edit_never_duplicates_or_rewrites_choice(client):
    saved=create(client)
    assert create(client)["id"]==saved["id"]
    changed=client.post("/api/entries",json=payload(client,parents_repayment="received"))
    assert changed.status_code==409
    changed=client.post("/api/entries",json=payload(client,parents_repayment=None))
    assert changed.status_code==409
    edit=payload(client,request_key="change-budget",parents_repayment="received",reason="Revisão explícita fictícia")
    r=client.post(f"/api/entries/{saved['id']}/edit",json=edit)
    assert r.status_code==200,r.text
    assert client.post(f"/api/entries/{saved['id']}/edit",json=edit).json()["id"]==r.json()["id"]
    assert budget(client,"2025-12")["total_expense_cents"]==10000
    assert budget(client,"2026-01")["total_expense_cents"]==0
    assert client.get(f"/api/entries/{saved['id']}").json()["parents_repayment"]=="pending"
    with transaction(client.app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(ParentBudgetChoice))==2

@pytest.mark.parametrize("changes",[
    {"responsible_id":1},{"kind":"income"},
    {"payment_method":"credit","card_id":1},{"occurred_on":"9999-12-01"},
])
def test_inapplicable_or_overflowing_choice_rolls_back_everything(client,changes):
    r=client.post("/api/entries",json=payload(client,**changes))
    assert r.status_code==422,r.text
    with transaction(client.app.state.engine) as s:
        assert all(s.scalar(select(func.count()).select_from(model))==0 for model in (Entry,Obligation,Invoice,ParentBudgetChoice))

def test_legacy_and_unindicated_entries_keep_original_month_and_combined_filter(client):
    load(client)
    a=create(client,parents_repayment=None,payment_method="cash",occurred_on="2025-01-10",request_key="old-cash")
    b=create(client,parents_repayment=None,payment_method="pix",occurred_on="2025-01-11",request_key="old-pix")
    assert a["parents_repayment"] is None and b["parents_repayment"] is None
    assert budget(client,"2025-01")["total_expense_cents"]==27000
    assert budget(client,"2025-02")["total_expense_cents"]==0
    data=client.get("/api/entries?payment_method=pix_debit").json()
    assert {row["id"] for row in data["items"]}=={a["id"],b["id"]}
    with transaction(client.app.state.engine) as s:
        assert s.get(Entry,a["id"]).payment_method=="cash"
        assert s.scalar(select(func.count()).select_from(ParentBudgetChoice))==0

def test_combined_payment_method_keeps_historical_duplicate_guard(client):
    def change(doc):
        doc["rows"][0]["proposal"].update(payment_method="cash",card_code=None,simulated_cycle=None)
    load(client,change)
    r=client.post("/api/entries",json=payload(client,description="Ficticio",responsible_id=1,
        total_cents=5000,occurred_on="2025-01-03",parents_repayment=None))
    assert r.status_code==409 and r.json()["error"]["code"]=="history_overlap"

def test_choice_is_immutable_and_eligibility_is_enforced_in_database(client):
    saved=create(client)
    for sql in ["UPDATE parent_budget_choices SET repayment_status='received'",
                "DELETE FROM parent_budget_choices"]:
        with pytest.raises(IntegrityError),transaction(client.app.state.engine) as s:
            s.connection().exec_driver_sql(sql)
    own=entry(client)
    with pytest.raises(IntegrityError),transaction(client.app.state.engine) as s:
        s.connection().exec_driver_sql("INSERT INTO parent_budget_choices VALUES(?, 'received', '2026-10-20')",(own["id"],))

def test_monthly_confirmation_can_choose_next_month_without_creating_cash(client):
    row=series(client,responsible_id=2,end_on="2026-10-01")
    r=decide(client,row["id"],parents_repayment="pending")
    assert r.status_code==200,r.text
    assert r.json()["entry"]["parents_repayment"]=="pending"
    assert budget(client,"2026-10")["total_expense_cents"]==0
    assert budget(client,"2026-11")["total_expense_cents"]==3000
    assert decide(client,row["id"],parents_repayment="pending").json()["entry"]["id"]==r.json()["entry"]["id"]
    with transaction(client.app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(FinancialEvent))==0
