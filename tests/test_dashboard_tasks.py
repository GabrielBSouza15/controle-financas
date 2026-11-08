"""Lista de ação reutiliza decisões e posição financeira sem criar fatos."""
from datetime import date
import pytest
from test_settlements import client, entry, event, allocations, post
from test_recurrences import series, decide
from test_dashboard import counts
from test_history_integration import load


@pytest.fixture(autouse=True)
def current_month(monkeypatch):
    monkeypatch.setattr("app.services.dashboard_tasks.today_local", lambda: date(2026,10,20))


def tasks(c, **params):
    r=c.get("/api/dashboard",params={"month":"2026-10","months":3,**params})
    assert r.status_code==200,r.text
    return r.json()["pending_tasks"]


def test_pending_recurrences_include_both_months_and_people_and_follow_decisions(client):
    rule=series(client)
    other=series(client,request_key="parent",responsible_id=2)
    series(client,request_key="later",start_on="2026-12-01")
    suspended=series(client,request_key="suspended")
    assert client.post(f"/api/recurrences/{suspended['id']}/changes",json={"request_key":"suspend","effective_month":"2026-10","suspended":True}).status_code==200
    before=counts(client)
    initial=tasks(client)
    assert initial["count"]==4
    assert initial["month_from"]=="2026-10-01" and initial["month_to"]=="2026-11-01"
    assert {r["responsible_code"] for r in initial["items"]}=={"self","parents"}
    assert tasks(client,month="2025-01",responsible_code="parents")==initial
    assert counts(client)==before
    assert decide(client,rule["id"]).status_code==200
    assert decide(client,other["id"],request_key="skip-parent",action="skip",occurred_on=None,actual_cents=None).status_code==200
    remaining=tasks(client)
    assert remaining["count"]==2
    assert all(r["reference_month"]=="2026-11-01" for r in remaining["items"])
    assert client.get("/api/settlements").json()["cash_out_cents"]==0


def test_unpaid_cards_use_remaining_amount_and_return_after_reversal(client):
    own=entry(client,total_cents=1000)
    entry(client,request_key="parents",responsible_id=2,total_cents=2000)
    entry(client,request_key="next",occurred_on="2026-10-20",total_cents=500)
    entry(client,request_key="later",occurred_on="2026-11-20",total_cents=800)
    before=counts(client)
    initial=tasks(client)
    assert [(r["reference_month"],r["amount_cents"]) for r in initial["items"]]==[("2026-10-01",3000),("2026-11-01",500)]
    assert counts(client)==before
    payment=post(client,event(client,amount_cents=600,allocations=allocations(own,600)))
    assert tasks(client)["items"][0]["amount_cents"]==2400
    payload=dict(request_key="whole",card_id=client.card,month="2026-10",expected_remaining_cents=2400,occurred_on="2026-10-05",account_id=client.account)
    response=client.post("/api/personal/bill",json=payload)
    assert response.status_code==200,response.text
    assert tasks(client)["count"]==1
    assert client.post(f"/api/settlements/{payment['id']}/reverse",json={"request_key":"reverse"}).status_code==200
    assert tasks(client)["items"][0]["amount_cents"]==600


def test_historical_and_new_card_share_one_item_and_old_paid_is_excluded(client,monkeypatch):
    monkeypatch.setattr("app.services.dashboard_tasks.today_local",lambda:date(2025,1,5))
    load(client)
    entry(client,occurred_on="2025-01-04",total_cents=2500)
    items=tasks(client)["items"]
    assert len(items)==1 and items[0]["kind"]=="invoice"
    assert items[0]["amount_cents"]==9500


def test_empty_and_year_boundary_are_explicit(client,monkeypatch):
    monkeypatch.setattr("app.services.dashboard_tasks.today_local",lambda:date(2026,12,20))
    empty=tasks(client)
    assert empty["count"]==0 and empty["items"]==[]
    assert empty["month_to"]=="2027-01-01"
    rule=series(client,start_on="2026-12-31")
    result=tasks(client)
    assert [(r["series_id"],r["scheduled_on"]) for r in result["items"]]==[(rule["id"],"2026-12-31"),(rule["id"],"2027-01-31")]


def test_forecast_only_card_is_not_payable_until_confirmed(client):
    rule=series(client,payment_method="credit",card_id=client.card,account_id=None)
    assert all(r["kind"]=="recurrence" for r in tasks(client)["items"])
    response=decide(client,rule["id"],actual_cents=2900)
    assert response.status_code==200,response.text
    result=tasks(client)
    bills=[r for r in result["items"] if r["kind"]=="invoice"]
    assert len(bills)==1 and bills[0]["amount_cents"]==2900
    assert len([r for r in result["items"] if r["kind"]=="recurrence"])==1
