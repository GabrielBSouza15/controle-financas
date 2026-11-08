"""Exportação integral, seleção financeira e PDF sem metadados de origem."""
from datetime import date
import pytest
from test_settlements import client, entry
from test_recurrences import series
from test_history_integration import load
from test_dashboard import counts
from app.services.expense_pdf import report_data, render_pdf, money


@pytest.mark.parametrize("person", ["self", "parents", "all"])
def test_report_matches_details_includes_history_forecasts_and_next_month(client, person):
    load(client)
    for who in (1, 2):
        entry(client, request_key=f"pix-{who}", responsible_id=who, payment_method="pix", card_id=None,
              occurred_on="2025-01-15", total_cents=12345, installment_count=2)
        entry(client, request_key=f"credit-{who}", responsible_id=who,
              occurred_on="2025-01-06", total_cents=9876)
    series(client, responsible_id=2, amount_cents=2345, start_on="2025-01-10")
    before = counts(client)
    data = report_data(client.app.state.engine, date(2025, 1, 1), person)
    for month, field in [("2025-01", "total_cents"), ("2025-02", "next_total_cents")]:
        expected = client.get("/api/dashboard/details", params=dict(month=month, metric="expense", responsible_code=person, limit=1)).json()
        assert data[field] == expected["total_cents"]
        if month == "2025-01":
            assert len(data["items"]) == expected["total"] > 1
    assert sum(amount for _, amount in data["methods"]) == data["total_cents"]
    assert data["registered_cents"] + data["forecast_cents"] == data["total_cents"]
    assert sum(v for _, v in data["people"]) == data["total_cents"]
    assert counts(client) == before


def test_pdf_response_filters_and_empty_year_rollover(client):
    entry(client, payment_method="pix", card_id=None, occurred_on="2026-12-05", total_cents=9876)
    entry(client, request_key="next", payment_method="pix", card_id=None, occurred_on="2027-01-05", total_cents=2345)
    own = report_data(client.app.state.engine, date(2026, 12, 1), "self")
    assert own["next_month"] == date(2027, 1, 1) and own["next_total_cents"] == 2345
    empty = report_data(client.app.state.engine, date(2026, 12, 1), "parents")
    assert empty["total_cents"] == empty["next_total_cents"] == 0 and not empty["items"]
    for person in ("self", "parents", "all"):
        r = client.get("/api/dashboard/expenses.pdf", params=dict(month="2026-12", responsible_code=person))
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["content-disposition"] == f'attachment; filename="despesas-2026-12-{person}.pdf"'
        assert r.content.startswith(b"%PDF-") and b"%%EOF" in r.content
    for params in [dict(month="bad"), dict(month="2026-12", responsible_code="other"), dict(month="9999-12"),
                   dict(month="2026-12", category_id=1, uncategorized=True)]:
        assert client.get("/api/dashboard/expenses.pdf", params=params).status_code == 422


def test_category_and_phase_filters_match_and_long_pdf_is_multipage(client, tmp_path):
    category = client.post("/api/categories", json={"name": "Categoria fictícia"}).json()["id"]
    for i in range(53):
        entry(client, request_key=f"item-{i}", description=f"Item {i}: Ação & café <teste> " + "texto " * 8,
              payment_method="pix", card_id=None, total_cents=101 + i,
              category_id=category, occurred_on="2026-11-03")
    series(client, start_on="2026-11-02", category_id=category, amount_cents=303)
    for phase in ("all", "registered", "forecast"):
        filters = dict(category_name="Categoria fictícia", phase=phase)
        data = report_data(client.app.state.engine, date(2026, 11, 1), "self", **filters)
        expected = client.get("/api/dashboard/details", params=dict(month="2026-11", metric="expense", **filters)).json()
        assert data["total_cents"] == expected["total_cents"] and len(data["items"]) == expected["total"]
        pdf = render_pdf(data)
        assert pdf.count(b"/Type /Page\n") >= (2 if phase != "forecast" else 1)
    assert money(9007199254740991) == "R$ 90.071.992.547.409,91"
