"""Seleção de responsável, cronologia global e suspensão sem apagar fatos."""
from datetime import date
from test_settlements import client, entry, event, post, allocations
from test_recurrences import series
from test_history_integration import load
from test_dashboard import counts

def overview(c, person="self", month="2025-01"):
    r=c.get("/api/dashboard",params={"month":month,"months":3,"responsible_code":person})
    assert r.status_code==200,r.text
    return r.json()

def detail(c, metric, person="parents", month="2025-01", **extra):
    r=c.get("/api/dashboard/details",params={"month":month,"metric":metric,"responsible_code":person}|extra)
    assert r.status_code==200,r.text
    return r.json()

def test_selected_responsible_reconciles_history_registered_forecast_and_income(client):
    load(client)
    category=client.post("/api/categories",json={"name":"Pais fictícios"}).json()["id"]
    for key,person,kind,amount in [
        ("mine",1,"expense",9000),("mine-income",1,"income",21000),
        ("parents",2,"expense",3000),("parents-income",2,"income",15000)]:
        entry(client,request_key=key,responsible_id=person,kind=kind,total_cents=amount,
              payment_method="pix",card_id=None,occurred_on="2025-01-10",
              category_id=category if key=="parents" else None)
    series(client,responsible_id=2,amount_cents=4000,start_on="2025-01-05",category_id=category)
    before=counts(client)
    own=overview(client)
    parents=overview(client,"parents")
    assert own["budget"]==parents["budget"]  # contrato anterior e caixa próprio preservados
    assert own["selected_budget"]["total_expense_cents"]==14000
    assert own["selected_budget"]["total_income_cents"]==51000
    row=parents["selected_budget"]
    assert (row["registered_expense_cents"],row["forecast_expense_cents"])==(10000,4000)
    assert (row["total_income_cents"],row["result_cents"],row["expense_count"])==(15000,1000,3)
    for metric,key in [("expense","total_expense_cents"),("income","total_income_cents"),("result","result_cents")]:
        data=detail(client,metric)
        assert data["total_cents"]==sum(item["contribution_cents"] for item in data["items"])==row[key]
        assert all(item["responsible_code"]=="parents" for item in data["items"])
    assert sum(item["total_cents"] for item in parents["categories"])==14000
    assert detail(client,"expense",category_id=category)["total_cents"]==7000
    assert parents["selected_months"][0]==row
    assert counts(client)==before

def test_parent_repayment_is_not_income_and_cash_does_not_become_parent_bank_balance(client):
    own=entry(client)
    parents=entry(client,request_key="parents",responsible_id=2)
    post(client,event(client,request_key="parents-repay",kind="repayment",invoice_id=None,
                      payer_id=2,amount_cents=40000,allocations=allocations(parents,40000)))
    data=overview(client,"parents","2026-10")
    assert data["selected_budget"]["total_income_cents"]==0
    assert data["selected_budget"]["total_expense_cents"]==150000
    assert data["budget"]["cash_in_cents"]==40000
    assert client.get("/api/dashboard/details?month=2026-10&metric=cash&responsible_code=parents").status_code==422

def test_budget_and_entry_chronology_precedes_pagination(client):
    for day,name in [(20,"A depois"),(10,"Z antes")]:
        entry(client,request_key=f"day-{day}",responsible_id=2,payment_method="pix",card_id=None,
              occurred_on=f"2026-10-{day}",total_cents=1000,description=name)
    series(client,responsible_id=2,start_on="2026-10-05",description="M previsão")
    data=detail(client,"expense",month="2026-10")
    assert [r["scheduled_on"] for r in data["items"]]==["2026-10-05","2026-10-10","2026-10-20"]
    page=detail(client,"expense",month="2026-10",limit=1,offset=1)
    assert page["total"]==3 and page["items"][0]["scheduled_on"]=="2026-10-10"
    entries=client.get("/api/entries?limit=1").json()
    assert entries["items"][0]["occurred_on"]=="2026-10-10"
    p=client.get("/api/projections?month_from=2026-10&months=3&responsible_id=2&limit=1&offset=1").json()
    assert p["items"][0]["scheduled_on"]=="2026-10-10"

def test_suspended_or_scheduled_series_hidden_before_pagination_and_recoverable(client,monkeypatch):
    monkeypatch.setattr("app.services.recurrences.today_local",lambda:date(2026,10,20))
    late=series(client,start_on="2026-11-13")
    early=series(client,request_key="other",start_on="2026-11-01")
    r=client.post(f"/api/recurrences/{late['id']}/changes",json={
        "request_key":"suspend","reason":"Suspensão futura fictícia","effective_month":"2026-11","suspended":True})
    assert r.status_code==200,r.text
    assert r.json()["listing_suspended"] and not r.json()["suspended"]
    hidden=client.get("/api/recurrences?visibility=active&limit=1").json()
    assert hidden["total"]==1 and hidden["items"][0]["id"]==early["id"]
    all_rows=client.get("/api/recurrences?visibility=all").json()
    assert all_rows["total"]==2
    assert [i["start_on"] for i in all_rows["items"]]==["2026-11-01","2026-11-13"]
    assert client.get("/api/recurrences?visibility=suspended").json()["items"][0]["id"]==late["id"]
    occurrences=client.get(f"/api/recurrences/{late['id']}/occurrences?month_from=2026-11&months=1").json()
    assert occurrences["items"][0]["status"]=="suspended"
    r=client.post(f"/api/recurrences/{late['id']}/changes",json={
        "request_key":"resume","reason":"Retomada fictícia","effective_month":"2026-12","suspended":False})
    assert r.status_code==200 and not r.json()["listing_suspended"]
    assert client.get("/api/recurrences?visibility=active").json()["total"]==2
    assert client.get("/api/entries").json()["total"]==0
