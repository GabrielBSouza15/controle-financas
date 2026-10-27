"""Adaptador de leitura: cada obrigação observada entra uma vez no orçamento."""
from sqlalchemy import select
from app.persistence.models import Card, Responsible
from .history_candidate import history_rows
from .history_events import totals, position
from app.persistence.entries import PersistenceValidationError

CARD_NAMES = {"nubank": "Nubank", "mercado_pago": "Mercado Pago/Livre"}

def references(session):
    people = {p.code: p.id for p in session.scalars(select(Responsible))}
    names = {c.name: c.id for c in session.scalars(select(Card))}
    return people, {code: names.get(name) for code, name in CARD_NAMES.items()}

def budget_rows(session):
    people, cards = references(session)
    from .history_state import records_state
    states=records_state(session)
    for row in history_rows(session):
        if row.role != "obligation":
            continue
        if row.responsible_code not in people or (row.card_code and cards.get(row.card_code) is None):
            raise PersistenceValidationError("Cadastros do histórico incompatíveis; confira os responsáveis e cartões.")
        category = (row.category_original or "").strip() or None
        yield {
            "month": states[row.id]["current_cycle"] or row.reference_on.replace(day=1),
            "kind": row.kind, "responsible_code": row.responsible_code,
            "amount_cents": states[row.id]["effective_cents"], "source": "history",
            "history_record_id": row.id, "source_row": row.source_row,
            "description": row.description, "number": row.observed_number,
            "installment_count": row.observed_count,
            "scheduled_on": row.reference_on.isoformat(),
            "card_id": cards.get(row.card_code), "responsible_id": people[row.responsible_code],
            "payment_method": row.payment_method, "category_id": None,
            "history_category": category, "category_name": category or "Sem categoria",
            "cycle_state": "simulated_unreconciled" if row.card_code else None,
            "paid_evidence": row.paid_evidence, "paid_on": None,
        }

def cycles(session, month):
    result = {}
    usage = totals(session)
    from .history_state import records_state
    states=records_state(session)
    for row in history_rows(session):
        if row.role != "obligation" or not row.card_code or states[row.id]["current_cycle"] != month:
            continue
        item = result.setdefault(row.card_code, {
            "card_code": row.card_code, "card_name": CARD_NAMES[row.card_code],
            "month": month.isoformat(), "total_cents": 0, "self_cents": 0, "parents_cents": 0,
            "paid_undated_cents": 0, "new_payment_cents": 0, "pending_cents": 0,
            "state": "simulated_unreconciled", "source_rows": [],
        })
        item["total_cents"] += states[row.id]["effective_cents"]
        item[row.responsible_code + "_cents"] += states[row.id]["effective_cents"]
        item["paid_undated_cents"] += row.amount_cents if row.paid_evidence else 0
        current = position(row, usage)
        item["new_payment_cents"] += current["new_payment_cents"]
        item["pending_cents"] += current["pending_cents"]
        item["source_rows"].append(row.source_row)
    from .history_operations import reconciliation
    from .history_state import cycle_payment
    for code,item in result.items():
        batch=next(r.batch_id for r in history_rows(session) if r.card_code==code and states[r.id]["current_cycle"]==month)
        item.update(cycle_payment(session,batch,code,month))
        item["reconciliation"]=reconciliation(session,code,month,item["total_cents"])
    return [result[key] for key in sorted(result)]
