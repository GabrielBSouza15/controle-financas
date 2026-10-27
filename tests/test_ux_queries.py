"""Regressões práticas dos filtros e da divisão financeira dos gráficos."""
import pytest
from test_settlements import client,entry
from test_history_integration import load
from test_dashboard import counts,dashboard
from test_invoice_credits import scenario,apply


def overview(c,**params):
    r=c.get("/api/invoices/overview",params=params)
    assert r.status_code==200,r.text
    return r.json()


def test_history_only_can_be_filtered_and_combined_with_new_entries(client):
    load(client)
    row=overview(client,card_id=client.card,month_from="2025-01",month_to="2025-01")["items"][0]
    assert (row["self_cents"],row["parents_cents"],row["statement_total_cents"])==(5000,7000,12000)
    assert row["source"]=="history" and row["status"]=="historical" and row["invoice_id"] is None
    entry(client,occurred_on="2025-01-03",total_cents=2500)
    data=overview(client,card_id=client.card,month_from="2025-01",month_to="2025-01",responsible_id=2)
    assert data["total"]==1
    row=data["items"][0]
    assert row["source"]=="mixed" and row["statement_total_cents"]==14500 and row["self_cents"]==7500
    d=client.get("/api/invoices/overview/details",params={"month":"2025-01","card_id":client.card,"limit":1,"offset":1}).json()
    assert d["total_cents"]==14500 and d["total"]==3 and len(d["items"])==1
    assert d["invoice_id"]==row["invoice_id"]
    assert overview(client,card_id=2,month_from="2025-01",month_to="2025-01")["total"]==0


def test_filters_combined_clear_pagination_and_no_writes(client):
    load(client)
    second=next(c["id"] for c in client.catalogs["cards"] if c["id"]!=client.card)
    entry(client,total_cents=10000,installment_count=3)
    entry(client,request_key="mp",card_id=second,total_cents=9000)
    before=counts(client)
    all_rows=overview(client)
    assert all_rows["total"]==5
    rows=overview(client,card_id=client.card,month_from="2026-10",month_to="2026-11",status="open")
    assert rows["total"]==2
    assert [r["reference_month"] for r in rows["items"]]==["2026-10-01","2026-11-01"]
    assert overview(client,card_id=second,responsible_id=2)["total"]==0
    assert overview(client,status="historical")["total"]==1
    assert overview(client,month_from="2030-01")["total"]==0
    page=overview(client,limit=1,offset=1)
    assert page["total"]==5 and len(page["items"])==1
    assert overview(client)==all_rows
    assert counts(client)==before


def test_card_chart_filter_preserves_kpis_and_excludes_pix_from_filtered_expense(client):
    second=next(c["id"] for c in client.catalogs["cards"] if c["id"]!=client.card)
    entry(client,total_cents=10000)
    entry(client,request_key="parents",responsible_id=2,total_cents=7000)
    entry(client,request_key="mp",card_id=second,total_cents=9000)
    entry(client,request_key="pix",payment_method="pix",card_id=None,total_cents=2000)
    original=dashboard(client)
    r=client.get("/api/dashboard",params={"month":"2026-10","months":3,"chart_card_id":client.card})
    assert r.status_code==200,r.text
    data=r.json();assert data["budget"]==original["budget"]
    first=data["outlook"]["months"][0]
    assert first["card_totals"]["self_cents"]==10000
    assert first["card_totals"]["parents_cents"]==7000
    assert first["card_totals"]["total_cents"]==17000
    assert first["expense_totals"]["total_cents"]==17000
    assert len(data["outlook"]["cards"])==1 and len(data["outlook"]["available_cards"])==2
    assert original["outlook"]["months"][0]["expense_totals"]["total_cents"]==28000
    assert client.get("/api/dashboard",params={"month":"2026-10","chart_card_id":9999}).status_code==422


def test_card_credits_never_assigned_to_parent_or_counted_as_expense(client):
    source,payment,target=scenario(client)
    assert apply(client,source,target).status_code==200
    months=dashboard(client)["outlook"]["months"]
    for i,budget,credit,bill in [(0,6000,4000,10000),(1,10000,-4000,6000)]:
        r=months[i]
        assert r["card_totals"]["self_cents"]==budget
        assert r["card_totals"]["parents_cents"]==0
        assert r["card_totals"]["credits_cents"]==credit
        assert r["card_totals"]["total_cents"]==bill
        assert r["expense_totals"]["total_cents"]==budget
        d=client.get("/api/dashboard/outlook/details",params={"month":r["month"][:7],"metric":"all_cards"}).json()
        assert d["total_cents"]==bill
        d=client.get("/api/dashboard/outlook/details",params={"month":r["month"][:7],"metric":"card_expense","card_id":client.card}).json()
        assert d["total_cents"]==budget
        invoice=client.get("/api/invoices/overview/details",params={"month":r["month"][:7],"card_id":client.card}).json()
        assert invoice["total_cents"]==bill
        assert any(x.get("source")=="statement_credit" for x in invoice["items"])


@pytest.mark.parametrize("params",[
    {"month_from":"2026-12","month_to":"2026-10"},{"month_from":"2026-13"},{"status":"paid"},{"card_id":0},
])
def test_invalid_invoice_filters_show_validation_without_writes(client,params):
    before=counts(client)
    assert client.get("/api/invoices/overview",params=params).status_code==422
    assert counts(client)==before


def test_closed_filter_keeps_mixed_origin_and_full_total(client):
    load(client)
    own=entry(client,occurred_on="2025-01-03",total_cents=2500)
    iid=own["obligations"][0]["invoice_id"]
    r=client.post(f"/api/invoices/{iid}/close",json={
        "request_key":"close-ux","reason":"Conferência fictícia dos novos lançamentos","expected_total_cents":2500})
    assert r.status_code==200,r.text
    data=overview(client,status="closed")
    assert data["total"]==1 and data["items"][0]["statement_total_cents"]==14500
    assert data["items"][0]["source"]=="mixed"
    assert data["items"][0]["operational_status"]=="closed"
    assert overview(client,status="historical")["total"]==1


def test_virtual_monthly_forecast_is_not_an_issued_invoice(client):
    from test_recurrences import series
    series(client,payment_method="credit",card_id=client.card,account_id=None)
    assert overview(client)["total"]==0
    assert dashboard(client)["outlook"]["months"][0]["card_totals"]["total_cents"]==3000
    detail=client.get("/api/invoices/overview/details",params={"card_id":client.card,"month":"2026-10"}).json()
    assert detail["total"]==0 and detail["total_cents"]==0


def test_invoice_estimates_use_card_cycle_without_account_or_writes(client):
    from test_recurrences import series
    series(client,start_on="2026-10-28",payment_method="credit",card_id=client.card,account_id=None)
    series(client,request_key="later",start_on="2026-11-13",payment_method="credit",card_id=client.card,account_id=None)
    series(client,request_key="pix",start_on="2026-11-01",payment_method="pix",card_id=None,account_id=None)
    before=counts(client)
    params=dict(card_id=client.card,month_from="2026-11",month_to="2026-12",include_forecasts=True)
    rows=overview(client,**params)["items"]
    assert [r["forecast_cents"] for r in rows]==[3000,6000]
    assert all(r["source"]=="forecast" and r["registered_count"]==0 for r in rows)
    assert all(r["statement_total_cents"]==0 and r["payment_remaining_cents"]==0 for r in rows)
    assert [r["projected_total_cents"] for r in rows]==[3000,6000]
    d=client.get("/api/invoices/overview/details",params={"card_id":client.card,"month":"2026-12","include_forecasts":True,"limit":1,"offset":1}).json()
    assert d["total"]==2 and len(d["items"])==1
    assert (d["total_cents"],d["registered_cents"],d["forecast_cents"])==(6000,0,6000)
    assert d["items"][0]["phase"]=="forecast"
    assert d["invoice_id"] is None and counts(client)==before
    assert overview(client,**params,responsible_id=2)["total"]==0
    assert overview(client,**params,status="closed")["total"]==0


def test_invoice_forecast_confirmation_suspension_and_skip_do_not_duplicate(client):
    from test_recurrences import series,decide
    row=series(client,start_on="2026-10-05",payment_method="credit",card_id=client.card,account_id=None)
    params=dict(card_id=client.card,month_from="2026-11",month_to="2026-11",include_forecasts=True)
    assert overview(client,**params)["items"][0]["projected_total_cents"]==3000
    assert decide(client,row["id"],occurred_on="2026-10-05",actual_cents=2901).status_code==200
    r=overview(client,**params)["items"][0]
    assert (r["statement_total_cents"],r["forecast_cents"],r["projected_total_cents"],r["payment_remaining_cents"])==(2901,0,2901,2901)
    change=client.post(f'/api/recurrences/{row["id"]}/changes',json={"request_key":"suspend-bill","reason":"Suspensão fictícia","effective_month":"2026-11","suspended":True})
    assert change.status_code==200,change.text
    assert overview(client,**(params|dict(month_from="2026-12",month_to="2026-12")))["total"]==0
    assert overview(client,**params)["items"][0]["projected_total_cents"]==2901
    other=series(client,request_key="other-forecast",start_on="2026-10-20",payment_method="credit",card_id=client.card,account_id=None,responsible_id=2)
    r=overview(client,**params,responsible_id=2)["items"][0]
    assert r["projected_total_cents"]==5901 and r["projected_self_cents"]==2901 and r["projected_parents_cents"]==3000
    assert r["payment_remaining_cents"]==2901
    assert decide(client,other["id"],request_key="skip-bill",action="skip",actual_cents=None,occurred_on=None).status_code==200
    assert overview(client,**params)["items"][0]["projected_total_cents"]==2901


def test_forecast_listing_has_bounded_horizon(client):
    from test_recurrences import series
    series(client,payment_method="credit",card_id=client.card,account_id=None)
    result=overview(client,month_from="2026-10",include_forecasts=True,limit=1,offset=1)
    assert result["total"]==12 and len(result["items"])==1
    assert result["forecast_month_to"]=="2027-09-01"
    assert client.get("/api/invoices/overview",params={"month_from":"2026-01","month_to":"2040-01","include_forecasts":True}).status_code==422
