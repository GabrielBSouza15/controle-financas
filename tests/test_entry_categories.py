"""Categorias sugeridas pelo histórico sem criar cadastros durante consultas."""
from sqlalchemy import func, select
from test_settlements import client, entry
from test_history_integration import load
from app.persistence.database import transaction
from app.persistence.models import Category, Entry, FinancialEvent, HistoryRecord


def test_catalog_suggests_only_observed_categories_and_keeps_originals(client):
    def change(doc):
        for row, name in zip(doc["rows"], ["  Educação  ", "Casa", "Educação", "Abertura", "Excluída"]):
            row["proposal"]["category_original"] = name
    load(client, change)
    for _ in range(2):
        catalog = client.get("/api/catalogs").json()
        assert catalog["categories"] == []
        assert catalog["category_suggestions"] == ["Casa", "Educação"]
    with transaction(client.app.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(Category)) == 0
        assert session.scalar(select(HistoryRecord.category_original).where(HistoryRecord.source_row == 3)) == "  Educação  "
        assert all(session.scalar(select(func.count()).select_from(model)) == 0 for model in (Entry, FinancialEvent))


def test_category_created_from_suggestion_is_reusable_and_does_not_rewrite_history(client):
    load(client, lambda doc: doc["rows"][0]["proposal"].update(category_original="  Educação  "))
    category = client.post("/api/categories", json={"name": " Educação ", "parent_id": None})
    assert category.status_code == 201
    identity = category.json()["id"]
    for kind in ("expense", "income"):
        saved = entry(client, request_key=f"category-{kind}", kind=kind, payment_method="pix",
                      card_id=None, category_id=identity, description=f"Categoria fictícia {kind}",
                      occurred_on="2026-10-20")
        assert saved["category_id"] == identity and saved["category_name"] == "Educação"
    assert client.post("/api/categories", json={"name": "Educação"}).status_code == 409
    catalog = client.get("/api/catalogs").json()
    assert catalog["categories"] == [{"id": identity, "name": "Educação", "parent_id": None}]
    assert catalog["category_suggestions"] == ["Educação"]
    with transaction(client.app.state.engine) as session:
        assert session.scalar(select(HistoryRecord.category_original).where(HistoryRecord.source_row == 3)) == "  Educação  "
        assert session.scalar(select(func.count()).select_from(FinancialEvent)) == 0
