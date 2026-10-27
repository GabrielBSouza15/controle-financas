"""Conferência independente dos indicadores com fatos financeiros fictícios."""
import pytest
from sqlalchemy import inspect
from test_settlements import client, entry, event, post, allocations
from test_recurrences import series, decide
from test_adjustments import adjustment, correction
from test_invoice_credits import scenario, apply
from test_legacy_web import legacy_client


def dashboard(c, month="2026-10", **params):
    response = c.get("/api/dashboard", params={"month": month, "months": 3} | params)
    assert response.status_code == 200, response.text
    return response.json()


def detail(c, metric, month="2026-10", **params):
    response = c.get("/api/dashboard/details", params={"month": month, "metric": metric} | params)
    assert response.status_code == 200, response.text
    return response.json()


def counts(c):
    with c.app.state.engine.connect() as conn:
        return {table: conn.exec_driver_sql('SELECT COUNT(*) FROM "' + table + '"').scalar()
                for table in inspect(conn).get_table_names()}


def test_empty_dashboard_is_read_only_and_has_explicit_scope(client):
    before = counts(client)
    data = dashboard(client)
    assert data["empty"] and data["categories"] == data["invoices"] == []
    assert all(data["budget"][key] == 0 for key in data["budget"] if key.endswith("_cents"))
    assert data["scope"]["includes_opening_balance"] is False
    assert data["scope"]["includes_legacy"] is False
    for metric in ["self_expense", "self_income", "parents_expense", "self_result", "cash"]:
        assert detail(client, metric)["total_cents"] == 0
    assert counts(client) == before
    assert client.post("/api/dashboard", json={}).status_code == 405


def test_three_thousand_invoice_does_not_duplicate_budget_or_repayment_as_income(client):
    own = entry(client)
    parents = entry(client, request_key="parents", responsible_id=2)
    post(client, event(client, amount_cents=300000, allocations=allocations(own) + allocations(parents)))
    post(client, event(client, request_key="repay", kind="repayment", invoice_id=None, payer_id=2,
                       amount_cents=40000, allocations=allocations(parents, 40000)))
    row = dashboard(client)["budget"]
    assert row["total_self_expense_cents"] == 150000 and row["parents_expense_cents"] == 150000
    assert row["total_self_income_cents"] == 0 and row["projected_self_result_cents"] == -150000
    assert row["cash_variation_cents"] == -260000
    invoice = dashboard(client)["invoices"][0]
    assert invoice["issuer_paid_cents"] == 300000 and invoice["parents_uncovered_cents"] == 110000
    for metric, amount in [("self_expense", 150000), ("self_income", 0), ("parents_expense", 150000),
                           ("self_result", -150000), ("cash", -260000)]:
        d = detail(client, metric)
        assert d["total_cents"] == sum(item["contribution_cents"] for item in d["items"]) == amount


def test_direct_payment_by_parents_has_no_own_cash(client):
    parents = entry(client, responsible_id=2)
    post(client, event(client, payer_id=2, account_id=None, amount_cents=40000, allocations=allocations(parents, 40000)))
    data = dashboard(client)
    assert data["invoices"][0]["issuer_paid_cents"] == 40000
    assert data["budget"]["cash_out_cents"] == 0
    assert detail(client, "cash")["total"] == 0


def test_forecast_and_confirmation_reconcile_with_categories_and_projection(client):
    category = client.post("/api/categories", json={"name": "Serviços fictícios"}).json()["id"]
    recurring = series(client, category_id=category)
    series(client, request_key="income", kind="income", amount_cents=200000)
    initial = dashboard(client)
    assert initial["budget"]["registered_self_expense_cents"] == 0
    assert initial["budget"]["forecast_self_expense_cents"] == 3000
    assert initial["categories"][0]["total_cents"] == 3000
    assert decide(client, recurring["id"], actual_cents=2901).status_code == 200
    data = dashboard(client)
    assert data["budget"]["registered_self_expense_cents"] == 2901
    assert data["budget"]["forecast_self_expense_cents"] == 0
    assert data["budget"]["cash_in_cents"] == data["budget"]["cash_out_cents"] == 0
    assert data["categories"][0]["registered_cents"] == 2901
    assert detail(client, "self_expense", category_id=category)["total_cents"] == 2901
    assert detail(client, "self_income", phase="forecast")["total_cents"] == 200000
    projection = client.get("/api/projections", params={"month_from": "2026-10", "months": 3}).json()
    assert data["months"] == projection["months"]


def test_category_totals_and_paginated_composition_include_every_obligation(client):
    first = client.post("/api/categories", json={"name": "Categoria A"}).json()["id"]
    second = client.post("/api/categories", json={"name": "Categoria B"}).json()["id"]
    entry(client, total_cents=10001, category_id=first)
    entry(client, request_key="second", total_cents=9999, category_id=second)
    entry(client, request_key="third", total_cents=1, payment_method="pix", card_id=None)
    data = dashboard(client)
    assert sum(row["total_cents"] for row in data["categories"]) == data["budget"]["total_self_expense_cents"] == 20001
    assert data["quality"]["uncategorized_expense_items"] == 1
    page = detail(client, "self_expense", limit=1, offset=1)
    assert len(page["items"]) == 1 and page["total"] == 3 and page["total_cents"] == 20001
    assert detail(client, "self_expense", category_id=first)["total_cents"] == 10001
    assert detail(client, "self_expense", uncategorized=True)["total_cents"] == 1


def test_installment_cycle_boundaries_and_year_change(client):
    entry(client, occurred_on="2026-12-05", total_cents=10000, installment_count=3)
    assert dashboard(client, month="2026-12")["budget"]["total_self_expense_cents"] == 0
    data = dashboard(client, month="2027-01")
    assert [row["registered_self_expense_cents"] for row in data["months"]] == [3334, 3333, 3333]
    assert detail(client, "self_expense", month="2027-01")["items"][0]["number"] == 1
    assert data["invoices"][0]["reference_month"] == "2027-01-01"


def test_cancelled_and_superseded_entries_are_not_counted(client):
    old = entry(client, total_cents=10000)
    cancelled = entry(client, request_key="cancelled", total_cents=5000)
    assert client.post(f'/api/entries/{cancelled["id"]}/cancel', json={"request_key": "cancel", "reason": "Cancelado antes da realização"}).status_code == 200
    payload = dict(request_key="edit", reason="Valor corrigido", description="Versão atual", occurred_on="2026-10-01",
                   total_cents=9000, installment_count=1, responsible_id=1, kind="expense", payment_method="credit", card_id=client.card)
    response = client.post(f'/api/entries/{old["id"]}/edit', json=payload)
    assert response.status_code == 200, response.text
    assert dashboard(client)["budget"]["total_self_expense_cents"] == 9000
    assert detail(client, "self_expense")["total"] == 1


def test_credit_carry_reduces_statement_but_not_destination_expense_again(client):
    source, payment, target = scenario(client)
    assert apply(client, source, target).status_code == 200
    october = dashboard(client)
    november = dashboard(client, month="2026-11")
    assert october["budget"]["total_self_expense_cents"] == 6000
    assert october["invoices"][0]["statement_total_cents"] == 10000
    assert november["budget"]["total_self_expense_cents"] == 10000
    assert november["invoices"][0]["statement_total_cents"] == 6000
    assert detail(client, "self_expense", month="2026-11")["total_cents"] == 10000
    assert october["budget"]["cash_variation_cents"] == -10000


def test_refund_transfer_and_annulment_have_exact_cash_composition(client):
    own = entry(client, total_cents=10000)
    payment = post(client, event(client, amount_cents=10000, allocations=allocations(own)))
    assert adjustment(client, own["obligations"][0]["obligation_id"], -4000).status_code == 200
    assert correction(client, payment, 4000).status_code == 200
    other = client.post("/api/accounts", json={"name": "Segunda conta fictícia"}).json()["id"]
    transfer = client.post("/api/adjustments/transfers", json={
        "request_key": "transfer", "reason": "Transferência própria", "source_account_id": client.account,
        "target_account_id": other, "occurred_on": "2026-10-06", "amount_cents": 1234})
    assert transfer.status_code == 200, transfer.text
    data = dashboard(client)
    assert (data["budget"]["cash_in_cents"], data["budget"]["cash_out_cents"]) == (5234, 11234)
    composition = detail(client, "cash")
    assert composition["total"] == 4 and composition["total_cents"] == -6000
    assert sum(item["contribution_cents"] for item in composition["items"] if item["kind"] == "transfer") == 0
    assert client.post(f'/api/adjustments/transfers/{transfer.json()["id"]}/reverse',
                       json={"request_key": "annul-transfer", "reason": "Informação incorreta"}).status_code == 200
    assert detail(client, "cash")["total"] == 2 and dashboard(client)["budget"]["cash_variation_cents"] == -6000


def test_cash_uses_actual_month_but_invoice_uses_cycle_and_current_payment_position(client):
    own = entry(client, occurred_on="2026-09-30", total_cents=10000)
    invoice_id = own["obligations"][0]["invoice_id"]
    post(client, event(client, invoice_id=invoice_id, occurred_on="2026-10-05",
                       amount_cents=10000, allocations=allocations(own)))
    assert dashboard(client, "2026-09")["budget"]["cash_variation_cents"] == 0
    assert dashboard(client)["budget"]["cash_variation_cents"] == -10000
    assert dashboard(client)["invoices"][0]["issuer_paid_cents"] == 10000


def test_invoice_quality_tracks_missing_difference_matched_and_stale(client):
    own = entry(client, total_cents=10000)
    assert dashboard(client)["invoices"][0]["reconciliation_state"] == "missing"
    for key, expected, state in [("different", 9900, "difference"), ("matched", 10000, "matched")]:
        assert client.post("/api/adjustments/invoices/1/reconcile", json={
            "request_key": key, "reason": "Conferência fictícia", "expected_total_cents": expected}).status_code == 200
        assert dashboard(client)["invoices"][0]["reconciliation_state"] == state
    assert dashboard(client)["quality"]["invoice_ids_needing_review"] == []
    assert adjustment(client, own["obligations"][0]["obligation_id"], -100).status_code == 200
    assert dashboard(client)["invoices"][0]["reconciliation_state"] == "stale"


def test_trial_data_never_enter_dashboard(legacy_client):
    before = counts(legacy_client)
    data = dashboard(legacy_client)
    assert data["empty"] and data["scope"]["legacy_batches_excluded"] == 1
    assert data["budget"]["total_self_expense_cents"] == data["budget"]["cash_variation_cents"] == 0
    assert detail(legacy_client, "self_expense")["total"] == 0
    assert counts(legacy_client) == before


@pytest.mark.parametrize("path,params", [
    ("/api/dashboard", {"month": "2026-13"}),
    ("/api/dashboard", {"month": "2026-10", "months": 4}),
    ("/api/dashboard", {"month": "9999-12", "months": 3}),
    ("/api/dashboard/details", {"month": "2026-10", "metric": "invalid"}),
    ("/api/dashboard/details", {"month": "2026-10", "metric": "cash", "phase": "forecast"}),
    ("/api/dashboard/details", {"month": "2026-10", "metric": "self_expense", "category_id": 1, "uncategorized": True}),
    ("/api/dashboard/details", {"month": "2026-10", "metric": "self_income", "category_id": 1}),
])
def test_invalid_queries_have_explicit_validation(client, path, params):
    assert client.get(path, params=params).status_code == 422


def test_large_individual_amount_is_exact_but_unsafe_aggregate_is_rejected(client):
    maximum = 2**53 - 1
    entry(client, total_cents=maximum, payment_method="pix", card_id=None)
    assert dashboard(client)["budget"]["total_self_expense_cents"] == maximum
    entry(client, request_key="overflow", total_cents=1, payment_method="pix", card_id=None)
    assert client.get("/api/dashboard", params={"month": "2026-10"}).status_code == 422
    assert client.get("/api/dashboard/details", params={"month": "2026-10", "metric": "self_expense"}).status_code == 422


def test_series_without_effective_card_rule_cannot_pollute_dashboard(client):
    before = counts(client)
    response = client.post("/api/recurrences", json={
        "request_key": "missing-rule", "reason": "Cenario ficticio", "description": "Mensal sem regra",
        "kind": "expense", "amount_cents": 3000, "start_on": "2019-10-01",
        "payment_method": "credit", "responsible_id": 1, "card_id": client.card,
    })
    assert response.status_code == 422
    assert counts(client) == before
    data = dashboard(client, month="2019-10")
    assert data["quality"]["missing_card_rule_items"] == 0
    assert detail(client, "self_expense", month="2019-10")["total"] == 0
