"""Gráficos têm a mesma composição integral das regras de orçamento e fatura."""
import pytest
from test_settlements import client, entry, event, post, allocations
from test_recurrences import series
from test_invoice_credits import scenario, apply
from test_dashboard import dashboard, counts
from test_history_integration import load


def details(c, month="2026-10", metric="all_expense", **kw):
    r = c.get("/api/dashboard/outlook/details", params={"month":month, "metric":metric}|kw)
    assert r.status_code == 200, r.text
    return r.json()


def test_all_people_methods_cards_and_forecasts_reconcile(client):
    second = next(card["id"] for card in client.catalogs["cards"] if card["id"] != client.card)
    entry(client,total_cents=10000)
    entry(client,request_key="parents",responsible_id=2,total_cents=7000,card_id=second)
    entry(client,request_key="pix",payment_method="pix",card_id=None,total_cents=2300)
    series(client,payment_method="credit",card_id=client.card,account_id=None,amount_cents=3000)
    series(client,request_key="parents-forecast",responsible_id=2,amount_cents=1000)
    before=counts(client)
    data=dashboard(client)
    row=data["outlook"]["months"][0]
    assert row["all_expense_cents"]==23300
    assert [s["amount_cents"] for s in row["expense_segments"]]==[12300,3000,7000,1000]
    assert [(s["start_cents"],s["end_cents"]) for s in row["expense_segments"]]==[(0,12300),(12300,15300),(15300,22300),(22300,23300)]
    assert {c["card_id"]:c["total_cents"] for c in row["cards"]}=={client.card:13000,second:7000}
    for card in row["cards"]:
        d=details(client,metric="card",card_id=card["card_id"])
        assert d["total_cents"]==sum(i["contribution_cents"] for i in d["items"])==card["total_cents"]
    d=details(client,limit=1,offset=1)
    assert d["total_cents"]==23300 and d["total"]==5 and len(d["items"])==1
    assert counts(client)==before


def test_history_and_new_entries_are_counted_once_without_cash(client):
    load(client)
    entry(client,occurred_on="2025-01-03",total_cents=2500)
    before=counts(client)
    data=dashboard(client,month="2025-01")
    row=data["outlook"]["months"][0]
    assert row["all_expense_cents"]==14500
    card=next(c for c in row["cards"] if c["card_id"]==client.card)
    assert card["total_cents"]==14500 and card["historical_items"]==2
    d=details(client,"2025-01","card",card_id=client.card)
    assert d["total_cents"]==14500 and len([i for i in d["items"] if i.get("history_record_id")])==2
    assert all(i["paid_on"] is None for i in d["items"] if i.get("history_record_id"))
    assert data["budget"]["cash_out_cents"]==0
    assert counts(client)==before


def test_payment_does_not_reduce_forecast_or_create_second_expense(client):
    own=entry(client,total_cents=10000)
    post(client,event(client,amount_cents=10000,allocations=allocations(own)))
    row=dashboard(client)["outlook"]["months"][0]
    assert row["cards"][0]["total_cents"]==row["all_expense_cents"]==10000
    assert details(client,metric="card",card_id=client.card)["total_cents"]==10000


def test_credit_transfer_changes_bill_but_never_duplicates_expense(client):
    source,payment,target=scenario(client)
    assert apply(client,source,target).status_code==200
    rows=dashboard(client)["outlook"]["months"]
    assert [(r["cards"][0]["total_cents"],r["all_expense_cents"]) for r in rows[:2]]==[(10000,6000),(6000,10000)]
    for month,total,credit in [("2026-10",10000,4000),("2026-11",6000,-4000)]:
        d=details(client,month,"card",card_id=client.card)
        assert d["total_cents"]==total==sum(i["contribution_cents"] for i in d["items"])
        assert next(i for i in d["items"] if i["kind"]=="statement_credit")["contribution_cents"]==credit
        assert not any(i["kind"]=="statement_credit" for i in details(client,month)["items"])


@pytest.mark.parametrize("params",[
    {"metric":"card"}, {"metric":"all_expense","card_id":1}, {"metric":"card","card_id":9999},
    {"metric":"wrong"}, {"metric":"card","card_id":0}, {"metric":"all_expense","offset":-1},
])
def test_invalid_chart_queries_are_rejected_without_writes(client,params):
    before=counts(client)
    assert client.get("/api/dashboard/outlook/details",params={"month":"2026-10"}|params).status_code==422
    assert counts(client)==before


def test_empty_and_year_boundary_chart_values(client):
    empty=dashboard(client)["outlook"]
    assert len(empty["months"])==3 and all(m["all_expense_cents"]==0 for m in empty["months"])
    entry(client,occurred_on="2026-12-05",total_cents=10000,installment_count=3)
    rows=dashboard(client,month="2027-01")["outlook"]["months"]
    assert [m["all_expense_cents"] for m in rows]==[3334,3333,3333]
    assert [m["cards"][0]["total_cents"] for m in rows]==[3334,3333,3333]


def test_income_columns_include_registered_forecast_and_keep_income_under_card_filter(client):
    entry(client,total_cents=1500)
    entry(client,request_key="salary",kind="income",payment_method="pix",card_id=None,total_cents=10001)
    series(client,request_key="salary-forecast",kind="income",description="Receita prevista fictícia",amount_cents=20002)
    before=counts(client)
    for params in ({}, {"chart_card_id":client.card}, {"responsible_code":"parents"}):
        data=dashboard(client,**params)
        rows=data["outlook"]["months"]
        assert [r["income_totals"]["total_cents"] for r in rows]==[30003,20002,20002]
        assert rows[0]["income_totals"]["self_cents"]==30003
        assert rows[0]["expense_totals"]["total_cents"]==1500
        result=details(client,metric="all_income",limit=1,offset=1)
        assert result["total_cents"]==30003 and result["total"]==2 and len(result["items"])==1
    assert client.get("/api/dashboard/outlook/details",params={"month":"2026-10","metric":"all_income","card_id":client.card}).status_code==422
    assert counts(client)==before


def test_confirmation_replaces_income_estimate_once_in_chart(client):
    from test_recurrences import decide
    rule=series(client,kind="income",amount_cents=12345)
    assert dashboard(client)["outlook"]["months"][0]["income_totals"]["total_cents"]==12345
    assert decide(client,rule["id"],expected_cents=12345,actual_cents=12344).status_code==200
    data=dashboard(client)
    assert data["outlook"]["months"][0]["income_totals"]["total_cents"]==12344
    result=details(client,metric="all_income")
    assert result["total_cents"]==12344 and result["total"]==1
    assert result["items"][0]["phase"]=="registered"
    assert data["budget"]["cash_in_cents"]==0


def test_historical_income_is_in_column_once_without_fabricating_receipt(client):
    load(client)
    data=dashboard(client,month="2025-01")
    result=details(client,month="2025-01",metric="all_income")
    assert data["outlook"]["months"][0]["income_totals"]["total_cents"]==result["total_cents"]==30000
    assert len(result["items"])==1 and result["items"][0]["source"]=="history"
    assert data["budget"]["cash_in_cents"]==0
