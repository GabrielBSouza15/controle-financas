"""Gráficos futuros derivados: cobranças de cartão e despesas de todos os responsáveis."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.persistence.models import Card, Invoice
from app.persistence.entries import PersistenceValidationError
from .projections import projection_in_session
from .invoices import invoice_summaries
from .settlements import safe


def card_adjustments(session, refs):
    """Crédito entre ciclos muda cobrança, não gera outra despesa."""
    rows = session.execute(select(Invoice, Card.name).join(Card).where(
        Invoice.reference_month.in_(refs)).order_by(Invoice.reference_month, Invoice.id)).all()
    items = []
    for invoice in invoice_summaries(session, rows).values():
        for field, sign, label in [
            ("credit_out_cents", 1, "Crédito levado para outra fatura"),
            ("credit_in_cents", -1, "Crédito recebido de outra fatura"),
        ]:
            amount = invoice[field] * sign
            if amount:
                items.append({
                    "month": invoice["reference_month"], "kind": "statement_credit",
                    "phase": "registered", "description": label, "amount_cents": safe(amount),
                    "contribution_cents": safe(amount), "card_id": invoice["card_id"],
                    "card_name": invoice["card_name"], "invoice_id": invoice["id"],
                    "scheduled_on": invoice["closing_date"], "source": "statement_credit",
                })
    return items


def graph_totals(own, parents, credits=0):
    budget=safe(own+parents)
    top=safe(budget+max(credits,0))
    return {"self_cents":safe(own),"parents_cents":safe(parents),"credits_cents":safe(credits),
            "total_cents":safe(budget+credits),"plot_min_cents":min(credits,0),"plot_max_cents":top,
            "segments":[{"person":"self","amount_cents":safe(own),"start_cents":0,"end_cents":safe(own)},
                        {"person":"parents","amount_cents":safe(parents),"start_cents":safe(own),"end_cents":budget},
                        {"person":"credit","amount_cents":safe(credits),"start_cents":budget if credits>0 else 0,
                         "end_cents":top if credits>0 else min(credits,0)}]}


def card_plot(items, credits):
    """Positions are computed in Python; each forecast retains its own segment."""
    segments = []
    start = 0
    for person in ("self", "parents"):
        for phase in ("registered", "forecast"):
            amount = safe(sum(i["amount_cents"] for i in items if i["responsible_code"] == person and i["phase"] == phase))
            end = safe(start + amount)
            segments.append(dict(person=person, phase=phase, amount_cents=amount, start_cents=start, end_cents=end))
            start = end
    total = safe(start + credits)
    end = safe(start + max(0, credits))
    segments.append(dict(person="credit", phase="registered", amount_cents=credits, start_cents=start if credits > 0 else 0, end_cents=end if credits > 0 else credits))
    return dict(segments=segments, total_cents=total, plot_min_cents=min(0, credits), plot_max_cents=end)


def build_outlook(session, data, items, card_id=None, *, income_items=None):
    from datetime import date
    refs = [date.fromisoformat(row["month"]) for row in data["months"]]
    adjustments = card_adjustments(session, refs)
    cards = [{"id": card.id, "name": card.name} for card in session.scalars(select(Card).order_by(Card.id))]
    available_cards = cards
    if card_id is not None:
        if not any(c["id"]==card_id for c in cards):
            raise PersistenceValidationError("Cartão não encontrado.")
        cards=[c for c in cards if c["id"]==card_id]
    income_items = items if income_items is None else income_items
    months = []
    for source in data["months"]:
        ref = source["month"]
        selected = [item for item in items if item["month"] == ref and item["kind"] == "expense"]
        incomes = [item for item in income_items if item["month"] == ref and item["kind"] == "income"]
        income_totals = graph_totals(*(sum(item["amount_cents"] for item in incomes if item["responsible_code"] == person) for person in ("self", "parents")))
        segments = []
        start = 0
        for person in ("self", "parents"):
            for phase in ("registered", "forecast"):
                value = safe(sum(item["amount_cents"] for item in selected
                                 if item["responsible_code"] == person and item["phase"] == phase))
                end = safe(start + value)
                segments.append({"responsible_code": person, "phase": phase, "amount_cents": value,
                                 "start_cents": start, "end_cents": end})
                start = end
        card_rows = []
        for card in cards:
            own = [item for item in selected if item.get("card_id") == card["id"]]
            credits = [item for item in adjustments if item["month"] == ref and item["card_id"] == card["id"]]
            registered = safe(sum(item["amount_cents"] for item in own if item["phase"] == "registered")
                              + sum(item["amount_cents"] for item in credits))
            forecast = safe(sum(item["amount_cents"] for item in own if item["phase"] == "forecast"))
            parts={person: safe(sum(item["amount_cents"] for item in own if item["responsible_code"]==person)) for person in ("self","parents")}
            card_rows.append({
                "card_id": card["id"], "card_name": card["name"],
                "plot": card_plot(own, safe(sum(item["amount_cents"] for item in credits))),
                "registered_cents": registered, "forecast_cents": forecast,
                "self_cents":parts["self"],"parents_cents":parts["parents"],
                "credits_cents":safe(sum(item["amount_cents"] for item in credits)),
                "total_cents": safe(registered + forecast),
                "historical_items": sum(item.get("source") == "history" for item in own),
                "missing_rule_items": sum(bool(item.get("missing_rule")) for item in own),
            })
        assert start == source["all_expense_cents"]
        months.append({
            "card_totals":graph_totals(*(sum(c[key] for c in card_rows) for key in ("self_cents","parents_cents","credits_cents"))),
            "expense_totals":graph_totals(*(sum(s["amount_cents"] for s in segments if s["responsible_code"]==person) for person in ("self","parents"))),
            "income_totals": income_totals,
            "month": ref, "all_expense_cents": start, "expense_segments": segments, "cards": card_rows})
    return {"cards": cards, "available_cards":available_cards,"selected_card_id":card_id, "months": months,
            "scope": "all_responsibles_by_budget_cycle_not_cash_or_unpaid_balance",
            "includes_monthly_forecasts": True, "income_scope": "all_responsibles_all_methods_independent_of_card_filter", "historical_cycles_are_estimates": True}


def details(engine, month, metric, *, card_id=None, limit=50, offset=0):
    from .dashboard import budget_items
    if (metric in {"card","card_expense"}) != (card_id is not None):
        raise PersistenceValidationError("Escolha um cartão apenas para os detalhes das faturas.")
    with Session(engine) as session:
        if card_id is not None and session.get(Card, card_id) is None:
            raise PersistenceValidationError("Cartão não encontrado.")
        data = projection_in_session(session, month, 1, limit=None)
        items = [dict(item, contribution_cents=item["amount_cents"])
                 for item in budget_items(session, data) if item["kind"] == ("income" if metric == "all_income" else "expense")
                 and (card_id is None or item.get("card_id") == card_id)
                 and (metric != "all_cards" or item.get("card_id") is not None)]
        if metric in {"card","all_cards"}:
            items.extend(item for item in card_adjustments(session, [month]) if card_id is None or item["card_id"] == card_id)
        total = safe(sum(item["contribution_cents"] for item in items))
        from .projections import budget_order
        items.sort(key=budget_order)
        return {"month": month.isoformat(), "metric": metric, "card_id": card_id,
                "total_cents": total, "total": len(items), "limit": limit, "offset": offset,
                "items": items[offset:offset + limit]}
