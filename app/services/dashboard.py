"""Indicadores de leitura derivados dos mesmos compromissos, ciclos e fatos de caixa."""
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.persistence.models import (
    Card, Category, Invoice, InvoiceReconciliation, LegacyImportBatch,
)
from app.persistence.entries import PersistenceValidationError
from .projections import projection_in_session, cash_records
from .invoices import invoice_summaries
from .adjustments import invoice_snapshot
from .settlements import safe, canonical
from .dashboard_outlook import build_outlook


def validate_period(months):
    if months not in (3, 6, 12):
        raise PersistenceValidationError("Escolha horizonte de 3, 6 ou 12 meses.")


def budget_items(session, data):
    categories = dict(session.execute(select(Category.id, Category.name)).all())
    cards = dict(session.execute(select(Card.id, Card.name)).all())
    return [
        dict(item, category_name=item.get("category_name") or categories.get(item.get("category_id"), "Sem categoria"),
             card_name=cards.get(item.get("card_id")))
        for item in data["items"]
    ]


def select_items(items, metric, *, category_id=None, history_category=None, uncategorized=False, phase="all", responsible_code="self"):
    if sum((category_id is not None, history_category is not None, uncategorized)) > 1:
        raise PersistenceValidationError("Escolha uma categoria ou sem categoria.")
    if (category_id is not None or history_category is not None or uncategorized) and metric not in {"self_expense", "expense"}:
        raise PersistenceValidationError("O filtro de categoria pertence às despesas do responsável.")
    selected = []
    for item in items:
        if phase != "all" and item["phase"] != phase:
            continue
        person = "parents" if metric == "parents_expense" else responsible_code if metric in {"expense", "income", "result"} else "self"
        if item["responsible_code"] != person:
            continue
        if metric not in {"self_result", "result"} and item["kind"] != ("income" if metric in {"self_income", "income"} else "expense"):
            continue
        if category_id is not None and item.get("category_id") != category_id:
            continue
        if history_category is not None and item.get("history_category") != history_category:
            continue
        if uncategorized and (item.get("category_id") is not None or item.get("history_category")):
            continue
        value = -item["amount_cents"] if metric in {"self_result", "result"} and item["kind"] == "expense" else item["amount_cents"]
        selected.append(dict(item, contribution_cents=value))
    return selected


def person_budget(items, month, responsible_code):
    selected = [item for item in items if item["responsible_code"] == responsible_code and item["month"] == month]
    row = {"month": month}
    for phase in ("registered", "forecast"):
        for kind in ("expense", "income"):
            row[f"{phase}_{kind}_cents"] = safe(sum(item["amount_cents"] for item in selected
                if item["phase"] == phase and item["kind"] == kind))
        row[f"{phase}_expense_count"] = sum(item["phase"] == phase and item["kind"] == "expense" for item in selected)
    for kind in ("expense", "income"):
        row[f"total_{kind}_cents"] = safe(row[f"registered_{kind}_cents"] + row[f"forecast_{kind}_cents"])
    row["result_cents"] = safe(row["total_income_cents"] - row["total_expense_cents"])
    row["expense_count"] = row["registered_expense_count"] + row["forecast_expense_count"]
    return row


def category_totals(items, responsible_code):
    categories = {}
    for item in select_items(items, "expense", responsible_code=responsible_code):
        key = ("history", item["history_category"]) if item.get("history_category") else ("catalog", item["category_id"])
        row = categories.setdefault(key, {
            "category_id": item["category_id"], "history_category": item.get("history_category"), "name": item["category_name"],
            "registered_cents": 0, "forecast_cents": 0, "item_count": 0,
        })
        row[item["phase"] + "_cents"] += item["amount_cents"]
        row["item_count"] += 1
    for row in categories.values():
        row["total_cents"] = safe(row["registered_cents"] + row["forecast_cents"])
    return sorted(categories.values(), key=lambda row: (-row["total_cents"], row["name"], row["history_category"] or "", row["category_id"] or 0))

def category_key(row):
    return ("history", row["history_category"]) if row.get("history_category") else ("catalog", row.get("category_id"))


def named_categories(rows):
    """Agrupar somente a leitura pelo nome; cadastros e origens não são alterados."""
    groups = {}
    for row in rows:
        if category_key(row) == ("catalog", None):
            continue
        group = groups.setdefault(row["name"], dict(name=row["name"], total_cents=0, registered_cents=0, forecast_cents=0, item_count=0))
        for field in ("total_cents", "registered_cents", "forecast_cents", "item_count"):
            group[field] = safe(group[field] + row[field])
    return sorted(groups.values(), key=lambda row: (-row["total_cents"], row["name"]))


def category_chart(rows):
    named = named_categories(rows)
    shown = [dict(r, filters={"category_name": r["name"]}) for r in named[:5]]
    if named[5:]:
        rest = {field: safe(sum(r[field] for r in named[5:])) for field in ("total_cents", "registered_cents", "forecast_cents", "item_count")}
        shown.append(dict(rest, name="Outros", filters={"other_categories": True}))
    shown.extend(dict(r, filters={"uncategorized": True}) for r in rows if category_key(r) == ("catalog", None))
    total = safe(sum(r["total_cents"] for r in rows))
    for r in shown:
        r["share_basis_points"] = r["total_cents"] * 10000 // total if total else 0
    return shown


def summary(engine, month, months=6, chart_card_id=None, responsible_code="self"):
    validate_period(months)
    if responsible_code not in {"self", "parents"}:
        raise PersistenceValidationError("Escolha Eu ou Pai/Mãe.")
    with Session(engine) as session:
        # Todas as perspectivas desta resposta compartilham a mesma fotografia de leitura.
        data = projection_in_session(session, month, months, limit=None)
        items = budget_items(session, data)
        current = [item for item in items if item["month"] == month.isoformat()]
        category_rows = category_totals(current, responsible_code)
        invoices = list(invoice_summaries(session, session.execute(
            select(Invoice, Card.name).join(Card).where(Invoice.reference_month == month)
            .order_by(Card.name, Invoice.id)
        ).all()).values())
        latest = {}
        for record in session.scalars(select(InvoiceReconciliation).join(Invoice).where(
                Invoice.reference_month == month).order_by(InvoiceReconciliation.id)):
            latest[record.invoice_id] = record
        needs_review = []
        for invoice in invoices:
            record = latest.get(invoice["id"])
            snapshot, total = invoice_snapshot(session, invoice["id"])
            state = "missing" if record is None else (
                "stale" if record.snapshot_json != canonical(snapshot)
                else "difference" if record.expected_total_cents != total else "matched"
            )
            invoice["reconciliation_state"] = state
            if state != "matched":
                needs_review.append(invoice["id"])
        from .history_budget import cycles
        historical_invoices = cycles(session, month)
        selected = data["months"][0]
        chart_data = data if chart_card_id is None else projection_in_session(session, month, months, card_id=chart_card_id, limit=None)
        chart_items = items if chart_card_id is None else budget_items(session, chart_data)
        return {
            "month": month.isoformat(), "horizon": months,
            "budget": selected, "months": data["months"],
            "responsible_code": responsible_code,
            "selected_budget": person_budget(items, month.isoformat(), responsible_code),
            "selected_months": [person_budget(items, row["month"], responsible_code) for row in data["months"]],
            "outlook": build_outlook(session, chart_data, chart_items, chart_card_id, income_items=items),
            "categories": category_rows, "category_chart": category_chart(category_rows), "invoices": invoices, "historical_invoices": historical_invoices,
            "quality": {
                "uncategorized_expense_items": sum(item["kind"] == "expense" and item["category_id"] is None and not item.get("history_category") for item in current),
                "missing_description_items": sum(not (item.get("description") or "").strip() for item in current),
                "forecast_items": sum(item["phase"] == "forecast" for item in current),
                "historical_items": sum(item.get("source") == "history" for item in current),
                "historical_simulated_cycles": len(historical_invoices),
                "missing_card_rule_items": sum(bool(item.get("missing_rule")) for item in current),
                "invoice_ids_needing_review": needs_review,
            },
            "empty": not items and not invoices and not any(
                row["cash_in_cents"] or row["cash_out_cents"] for row in data["months"]),
            "scope": {
                "budget": "active_obligations_and_undecided_forecasts_by_cycle",
                "cash": "all_own_accounts_by_actual_date", "cash_is_partial": True,
                "includes_opening_balance": False, "includes_legacy": False,
                "includes_history": any(item.get("source") == "history" for item in items),
                "legacy_batches_excluded": session.scalar(select(func.count()).select_from(LegacyImportBatch)),
                "invoice_payment_position": "current_recorded_position",
            },
        }


def details(engine, month, metric, *, category_id=None, history_category=None, uncategorized=False, other_categories=False, category_name=None, phase="all", limit=50, offset=0, responsible_code="self"):
    if category_name is not None and (metric != "expense" or category_id is not None or history_category is not None or uncategorized or other_categories):
        raise PersistenceValidationError("Escolha o nome da categoria apenas nas despesas, sem combinar outros filtros de categoria.")
    if other_categories and (metric != "expense" or category_id is not None or history_category is not None or uncategorized):
        raise PersistenceValidationError("Outros se aplica apenas às demais categorias de despesas.")
    with Session(engine) as session:
        if metric == "cash":
            if responsible_code != "self" or phase != "all" or category_id is not None or history_category is not None or uncategorized:
                raise PersistenceValidationError("A composição do caixa usa somente o mês efetivo.")
            items = [
                dict(item, contribution_cents=item["amount_cents"] if item["direction"] == "in" else -item["amount_cents"])
                for item in cash_records(session) if item["occurred_on"][:7] == month.isoformat()[:7]
            ]
            items.sort(key=lambda item: (item["occurred_on"], item["kind"], item["id"], item["direction"]))
        else:
            data = projection_in_session(session, month, 1, limit=None)
            all_items = budget_items(session, data)
            excluded = set()
            if other_categories:
                ranked = named_categories(category_totals(all_items, responsible_code))
                excluded = {r["name"] for r in ranked[:5]}
            items = select_items(all_items, metric,
                                 category_id=category_id, history_category=history_category, uncategorized=uncategorized, phase=phase, responsible_code=responsible_code)
            if other_categories:
                items = [item for item in items if category_key(item) != ("catalog", None) and item["category_name"] not in excluded]
            if category_name is not None:
                items = [item for item in items if category_key(item) != ("catalog", None) and item["category_name"] == category_name]
        total_cents = safe(sum(item["contribution_cents"] for item in items))
        return {
            "month": month.isoformat(), "metric": metric, "phase": phase,
            "total": len(items), "total_cents": total_cents,
            "items": items[offset:offset + limit], "limit": limit, "offset": offset,
        }
