"""Visual read models conserve budget scopes, categories and signed invoice credits."""
from test_settlements import client,entry
from test_dashboard import dashboard,counts,detail
from test_recurrences import series
from test_history_integration import load
from test_invoice_credits import scenario,apply


def test_top_categories_other_and_uncategorized_reconcile_every_origin(client):
    cats=[]
    for i in range(7):
        cat=client.post("/api/categories",json={"name":f"Categoria {i}"}).json()["id"]
        cats.append(cat)
        entry(client,request_key=f"cat-{i}",category_id=cat,total_cents=(i+1)*100,payment_method="pix",card_id=None)
    series(client,category_id=cats[0],amount_cents=10)
    entry(client,request_key="none",total_cents=99,payment_method="pix",card_id=None)
    entry(client,request_key="parents",responsible_id=2,total_cents=999,payment_method="pix",card_id=None)
    before=counts(client)
    for person in ("self","parents"):
        data=dashboard(client,responsible_code=person)
        rows=data["category_chart"]
        assert sum(r["total_cents"] for r in rows)==data["selected_budget"]["total_expense_cents"]
        assert sum(r["share_basis_points"] for r in rows)<=10000
        for row in rows:
            result=detail(client,"expense",responsible_code=person,limit=1,**row["filters"])
            assert result["total_cents"]==row["total_cents"]
        if person=="self":
            assert len(rows)==7
            other=next(r for r in rows if r["name"]=="Outros")
            assert other["total_cents"]==310
            page=detail(client,"expense",other_categories=True,limit=1,offset=1)
            assert page["total"]==3 and page["total_cents"]==310
    assert counts(client)==before


def test_other_filter_rejects_incompatible_metrics_and_categories(client):
    for params in [dict(metric="income"),dict(metric="cash"),dict(metric="result"),dict(metric="expense",uncategorized=True),dict(metric="expense",category_id=1)]:
        assert client.get("/api/dashboard/details",params=dict(month="2026-10",other_categories=True)|params).status_code==422
    assert detail(client,"expense",other_categories=True)["total_cents"]==0


def test_card_filter_never_changes_personal_budget_categories_or_result(client):
    entry(client,total_cents=1999)
    entry(client,request_key="pix",payment_method="pix",card_id=None,total_cents=333)
    series(client,kind="income",amount_cents=5000)
    original=dashboard(client)
    filtered=dashboard(client,chart_card_id=client.card)
    for field in ("selected_budget","selected_months","category_chart","categories"):
        assert original[field]==filtered[field]
    for row in original["selected_months"]:
        assert detail(client,"result",month=row["month"][:7])["total_cents"]==row["result_cents"]


def test_card_segments_preserve_registered_forecast_and_people(client):
    entry(client,total_cents=1100)
    entry(client,request_key="parents",responsible_id=2,total_cents=2200)
    series(client,payment_method="credit",card_id=client.card,account_id=None,amount_cents=3300)
    series(client,request_key="parent-forecast",responsible_id=2,payment_method="credit",card_id=client.card,account_id=None,amount_cents=4400)
    row=dashboard(client)["outlook"]["months"][0]["cards"][0]
    assert [s["amount_cents"] for s in row["plot"]["segments"]]==[1100,3300,2200,4400,0]
    assert row["plot"]["segments"][3]["end_cents"]==row["total_cents"]==11000


def test_signed_card_credit_keeps_correct_plot_bounds(client):
    source,payment,target=scenario(client)
    assert apply(client,source,target).status_code==200
    rows=dashboard(client)["outlook"]["months"]
    assert rows[0]["cards"][0]["plot"]["total_cents"]==10000
    assert rows[1]["cards"][0]["plot"]["total_cents"]==6000
    assert rows[1]["cards"][0]["plot"]["plot_min_cents"]==-4000
    assert rows[1]["cards"][0]["plot"]["plot_max_cents"]==10000


def test_category_name_unifies_chart_and_preserves_source_identities(client):
    load(client,lambda doc:doc["rows"][0]["proposal"].update(category_original="Igual"))
    cat=client.post("/api/categories",json={"name":"Igual"}).json()["id"]
    entry(client,occurred_on="2025-01-03",total_cents=201,category_id=cat)
    rows=dashboard(client,month="2025-01")["category_chart"]
    same=[r for r in rows if r["name"]=="Igual"]
    assert len(same)==1 and same[0]["total_cents"]==5201
    result=detail(client,"expense",month="2025-01",**same[0]["filters"])
    assert result["total_cents"]==5201 and result["total"]==2
    assert any(i.get("history_record_id") for i in result["items"])
    assert any(i.get("entry_id") for i in result["items"])
    raw=dashboard(client,month="2025-01")["categories"]
    assert len([r for r in raw if r["name"]=="Igual"])==2


def test_named_category_filter_cannot_leak_into_other_metrics(client):
    for params in [dict(metric="cash"),dict(metric="income"),dict(metric="result"),dict(metric="expense",uncategorized=True),dict(metric="expense",other_categories=True),dict(metric="expense",category_id=1)]:
        assert client.get("/api/dashboard/details",params=dict(month="2026-10",category_name="Teste")|params).status_code==422
