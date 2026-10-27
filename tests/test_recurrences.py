from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from test_settlements import client, entry
from app.persistence.database import transaction
from app.persistence.models import Entry, FinancialEvent, RecurrenceSeries, RecurrenceOccurrence

def series(c,**kw):
    data={"request_key":"monthly","reason":"Previsao ficticia","description":"Assinatura ficticia","kind":"expense",
        "amount_cents":3000,"start_on":"2026-10-01","payment_method":"pix","responsible_id":1,"account_id":c.account}
    r=c.post("/api/recurrences",json=data|kw);assert r.status_code==200,r.text;return r.json()

def decide(c,identity,month="2026-10",**kw):
    data={"request_key":"confirm-month","reason":"Ocorrencia conferida","action":"confirm","expected_cents":3000,"actual_cents":3000,"occurred_on":"2026-10-01"}
    return c.post(f"/api/recurrences/{identity}/occurrences/{month}",json=data|kw)

def projection(c,start="2026-10",**kw):
    r=c.get("/api/projections",params={"month_from":start,"months":3}|kw);assert r.status_code==200,r.text;return r.json()

def test_consulting_forecasts_does_not_materialize_entries_or_cash(client):
    row=series(client)
    assert client.get(f'/api/recurrences/{row["id"]}/occurrences',params={"month_from":"2026-10","months":3}).json()["items"][0]["status"]=="forecast"
    p=projection(client);assert [r["forecast_self_expense_cents"] for r in p["months"]]==[3000]*3
    with transaction(client.app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(Entry))==0
        assert s.scalar(select(func.count()).select_from(FinancialEvent))==0
        assert s.scalar(select(func.count()).select_from(RecurrenceOccurrence))==0

def test_confirmation_replaces_forecast_once_and_is_not_payment(client):
    row=series(client)
    r=decide(client,row["id"],actual_cents=2901);assert r.status_code==200,r.text
    assert decide(client,row["id"],actual_cents=2901).json()==r.json()
    assert decide(client,row["id"],request_key="different").status_code==409
    p=projection(client);october=p["months"][0]
    assert october["registered_self_expense_cents"]==2901 and october["forecast_self_expense_cents"]==0
    assert october["cash_out_cents"]==0 and october["projected_self_result_cents"]==-2901
    assert p["total"]==3
    assert client.get("/api/entries").json()["total"]==1

@pytest.mark.parametrize("start,expected",[
    ("2024-01-31",["2024-01-31","2024-02-29","2024-03-31"]),
    ("2025-01-31",["2025-01-31","2025-02-28","2025-03-31"]),
    ("2024-12-30",["2024-12-30","2025-01-30","2025-02-28"]),
])
def test_anchor_day_survives_short_months_and_year_boundary(client,start,expected):
    row=series(client,start_on=start)
    r=client.get(f'/api/recurrences/{row["id"]}/occurrences',params={"month_from":start[:7],"months":3})
    assert [x["scheduled_on"] for x in r.json()["items"]]==expected

def test_price_changes_suspend_resume_and_confirmed_snapshot(client):
    row=series(client);assert decide(client,row["id"]).status_code==200
    path=f'/api/recurrences/{row["id"]}/changes'
    for data in [
        {"request_key":"price","effective_month":"2026-10","amount_cents":4000},
        {"request_key":"suspend","effective_month":"2026-11","suspended":True},
        {"request_key":"resume","effective_month":"2026-12","suspended":False},
    ]:
        r=client.post(path,json={"reason":"Alteracao ficticia"}|data);assert r.status_code==200,r.text
    occurrences=client.get(f'/api/recurrences/{row["id"]}/occurrences',params={"month_from":"2026-10","months":3}).json()["items"]
    assert [(o["status"],o["amount_cents"]) for o in occurrences]==[("confirmed",3000),("suspended",4000),("forecast",4000)]
    p=projection(client)
    assert [x["registered_self_expense_cents"]+x["forecast_self_expense_cents"] for x in p["months"]]==[3000,0,4000]

def test_outdated_expected_price_cannot_confirm(client):
    row=series(client)
    assert client.post(f'/api/recurrences/{row["id"]}/changes',json={"request_key":"price","reason":"Reajuste","effective_month":"2026-10","amount_cents":4000}).status_code==200
    assert decide(client,row["id"]).status_code==409
    assert client.get("/api/entries").json()["total"]==0

def test_skip_is_not_realization_or_cash_and_end_limits(client):
    row=series(client,end_on="2026-11-01")
    r=decide(client,row["id"],action="skip",actual_cents=None,occurred_on=None);assert r.status_code==200,r.text
    items=client.get(f'/api/recurrences/{row["id"]}/occurrences',params={"month_from":"2026-10","months":3}).json()["items"]
    assert [x["status"] for x in items]==["skipped","forecast","outside"]
    assert client.get("/api/entries").json()["total"]==0
    assert [x["forecast_self_expense_cents"] for x in projection(client)["months"]]==[0,3000,0]

def test_card_projection_uses_closing_cycle_and_previous_month(client):
    row=series(client,start_on="2026-10-05",payment_method="credit",card_id=client.card,account_id=None)
    p=projection(client,start="2026-11",card_id=client.card)
    assert [x["forecast_expense_cents"] for x in p["months"]]==[3000,3000,3000]
    assert p["items"][0]["reference_month"]=="2026-10-01"
    assert client.get("/api/invoices").json()["total"]==0
    # A realizacao em 04/10 muda para o ciclo de outubro; nao sobra previsao em novembro.
    assert decide(client,row["id"],occurred_on="2026-10-04").status_code==200
    p=projection(client)
    assert [x["registered_expense_cents"] for x in p["months"]]==[3000,0,0]
    assert [x["forecast_expense_cents"] for x in p["months"]]==[0,0,3000]

def test_parents_and_income_are_separate_and_filters_apply_to_budget(client):
    series(client,responsible_id=2)
    series(client,request_key="salary",description="Salario ficticio",kind="income",amount_cents=200000)
    p=projection(client)
    assert p["months"][0]["parents_expense_cents"]==3000
    assert p["months"][0]["projected_self_result_cents"]==200000
    mine=projection(client,responsible_id=1)
    assert mine["months"][0]["parents_expense_cents"]==0
    assert mine["total"]==3
    assert mine["cash_scope"]=="all_accounts_unfiltered" and mine["cash_is_partial"]

@pytest.mark.parametrize("change",[
    {"amount_cents":True},{"amount_cents":"100"},{"amount_cents":0},{"amount_cents":1.5},
    {"payment_method":"credit","card_id":None},{"kind":"income","payment_method":"credit","card_id":1},
    {"end_on":"2026-01-01"},{"account_id":999},{"responsible_id":999},{"description":" "},
])
def test_invalid_series_do_not_leave_records(client,change):
    data={"request_key":"invalid","reason":"Teste","description":"Mensal","kind":"expense","amount_cents":3000,
        "start_on":"2026-10-01","payment_method":"pix","responsible_id":1,"account_id":client.account}
    r=client.post("/api/recurrences",json=data|change)
    assert r.status_code in {404,422},r.text
    assert client.get("/api/recurrences").json()["total"]==0

@pytest.mark.parametrize("kw",[
    {"occurred_on":"9999-01-01"},{"occurred_on":"2026-09-30"},{"actual_cents":None},
    {"action":"skip","occurred_on":"2026-10-01"},{"actual_cents":True},
])
def test_bad_occurrence_does_not_create_entry(client,kw):
    row=series(client);assert decide(client,row["id"],**kw).status_code==422
    assert client.get("/api/entries").json()["total"]==0

def test_concurrent_confirmation_has_one_winner(client):
    row=series(client)
    def attempt(n):return decide(client,row["id"],request_key=f"concurrent-{n}").status_code
    with ThreadPoolExecutor(max_workers=2) as pool:status=list(pool.map(attempt,[1,2]))
    assert status.count(200)==1 and all(x in {200,409,503} for x in status)
    assert client.get("/api/entries").json()["total"]==1

def test_recurrence_keys_and_history_are_protected(client):
    row=series(client)
    assert client.post("/api/recurrences",json={"request_key":"monthly","reason":"Diferente","description":"Outro","kind":"expense","amount_cents":1,"start_on":"2026-10-01","payment_method":"pix","responsible_id":1}).status_code==409
    assert client.post("/api/entries",json={"request_key":"monthly","description":"Outro","kind":"expense","total_cents":1,"installment_count":1,"occurred_on":"2026-10-01","payment_method":"pix","responsible_id":1}).status_code in {409,422}
    assert decide(client,row["id"]).status_code==200
    for sql in ["DELETE FROM recurrence_occurrences","UPDATE recurrence_series SET amount_cents=1","UPDATE recurrence_occurrences SET expected_cents=1"]:
        with pytest.raises(IntegrityError):
            with client.app.state.engine.begin() as conn:conn.exec_driver_sql(sql)

def test_projection_pagination_totals_and_horizons(client):
    series(client)
    for horizon in (3,6,12):
        p=projection(client,months=horizon,limit=1,offset=1)
        assert len(p["months"])==horizon and p["total"]==horizon and len(p["items"])==1
    assert client.get("/api/projections",params={"month_from":"2026-10","months":4}).status_code==422
    assert client.get("/api/projections",params={"month_from":"9999-12","months":12}).status_code==422

def test_cancelling_confirmed_entry_does_not_regenerate_forecast(client):
    row=series(client);confirmed=decide(client,row["id"]).json()
    assert client.post(f'/api/entries/{confirmed["entry"]["id"]}/cancel',json={"request_key":"cancel-confirmed","reason":"Compromisso cancelado"}).status_code==200
    p=projection(client)
    assert p["months"][0]["registered_expense_cents"]==0 and p["months"][0]["forecast_expense_cents"]==0
