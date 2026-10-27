"""Faturas por ciclo, somando somente obrigações de versões ativas."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.persistence.models import Card, Category, Entry, Invoice, InvoiceClosure, Obligation, Responsible
from app.persistence.queries import active_entry_filter
from app.persistence.entries import PersistenceValidationError
from .catalogs import NotFoundError
from .entries import MAX_WEB_INTEGER
from app.persistence.settlements import allocation_totals, effective_amounts
from app.persistence.invoice_credits import credit_totals, invoice_bank_totals


def invoice_summaries(session, invoices):
    """Acumular em int Python evita também overflow do SUM de 64 bits do SQLite."""
    result = {}
    for invoice, card_name in invoices:
        result[invoice.id] = {
            "id": invoice.id, "card_id": invoice.card_id, "card_name": card_name,
            "reference_month": invoice.reference_month.isoformat(),
            "closing_date": invoice.closing_date.isoformat(), "due_date": invoice.due_date.isoformat(),
            "status": invoice.status, "total_cents": 0, "self_cents": 0, "parents_cents": 0,
            "active_obligation_count": 0, "issuer_paid_cents": 0, "parents_covered_cents": 0,
        }
    if not result:
        return result
    statement = (select(Obligation.id, Obligation.invoice_id, Obligation.amount_cents, Responsible.code)
        .join(Entry, Entry.id == Obligation.entry_id)
        .join(Responsible, Responsible.id == Entry.responsible_id)
        .where(Obligation.invoice_id.in_(result), active_entry_filter()))
    rows = session.execute(statement).all()
    settled = allocation_totals(session, [row.id for row in rows])
    deltas = effective_amounts(session, [row.id for row in rows])
    for obligation_id, invoice_id, amount, code in rows:
        amount += deltas[obligation_id]
        item = result[invoice_id]
        item["total_cents"] += amount
        item[f"{code}_cents"] += amount
        item["active_obligation_count"] += 1
        item["parents_covered_cents"] += settled[obligation_id]["parents_covered_cents"]
    banks = invoice_bank_totals(session, result)
    credits = credit_totals(session, result)
    for item in result.values():
        item.update(credits[item["id"]])
        item["issuer_paid_cents"] = banks[item["id"]]["paid_cents"]
        item["issuer_unallocated_cents"] = banks[item["id"]]["unallocated_cents"]
        item["statement_total_cents"] = item["total_cents"] + item["credit_out_cents"] - item["credit_in_cents"]
        if item["total_cents"] > MAX_WEB_INTEGER:
            raise PersistenceValidationError("O total desta fatura excede a faixa exata suportada pela interface web.")
        item["empty"] = item["active_obligation_count"] == 0
        item["issuer_remaining_cents"] = item["statement_total_cents"] - item["issuer_paid_cents"]
        item["parents_uncovered_cents"] = item["parents_cents"] - item["parents_covered_cents"]
        for key,value in item.items():
            if key.endswith("_cents") and abs(value)>MAX_WEB_INTEGER:
                raise PersistenceValidationError("O total agregado excede a faixa exata da interface web.")
        item["liquidation_status"] = "empty" if item["empty"] else "paid" if item["issuer_remaining_cents"] == 0 else "credit" if item["issuer_remaining_cents"] < 0 else "partial" if item["issuer_paid_cents"] or item["credit_in_cents"] else "none"
    return result


def list_invoices(engine, *, card_id=None, responsible_id=None, month_from=None, month_to=None,
                  status=None, limit=50, offset=0):
    conditions = []
    if month_from and month_to and month_from > month_to:
        raise PersistenceValidationError("O ciclo inicial deve ser anterior ou igual ao final.")
    if card_id is not None:
        conditions.append(Invoice.card_id == card_id)
    if month_from is not None:
        conditions.append(Invoice.reference_month >= month_from)
    if month_to is not None:
        conditions.append(Invoice.reference_month <= month_to)
    if status is not None:
        conditions.append(Invoice.status == status)
    if responsible_id is not None:
        # Selecionar a fatura, sem reduzir o valor completo exibido no resumo.
        has_person = (select(Obligation.id).join(Entry, Entry.id == Obligation.entry_id)
            .where(Obligation.invoice_id == Invoice.id, Entry.responsible_id == responsible_id,
                   active_entry_filter()).exists())
        conditions.append(has_person)
    with Session(engine) as session:
        total = session.scalar(select(func.count()).select_from(Invoice).where(*conditions))
        page = session.execute(select(Invoice, Card.name).join(Card).where(*conditions)
            .order_by(Invoice.reference_month, Invoice.closing_date, Card.name, Invoice.id)
            .limit(limit).offset(offset)).all()
        items = list(invoice_summaries(session, page).values())
        return {"items": items, "total": total, "limit": limit, "offset": offset}


def get_invoice(engine, invoice_id, *, limit=50, offset=0):
    with Session(engine) as session:
        row = session.execute(select(Invoice, Card.name).join(Card).where(Invoice.id == invoice_id)).first()
        if row is None:
            raise NotFoundError("A fatura não foi encontrada.")
        result = invoice_summaries(session, [row])[invoice_id]
        # Resumo e página de parcelas usam a mesma transação de leitura.
        statement = (select(Obligation, Entry, Responsible.name, Category.name)
            .join(Entry, Entry.id == Obligation.entry_id)
            .join(Responsible, Responsible.id == Entry.responsible_id)
            .outerjoin(Category, Category.id == Entry.category_id)
            .where(Obligation.invoice_id == invoice_id, active_entry_filter())
            .order_by(Entry.occurred_on, Entry.id, Obligation.number)
            .limit(limit).offset(offset))
        result["items"] = [{
            "obligation_id": part.id, "entry_id": entry.id, "description": entry.description,
            "occurred_on": entry.occurred_on.isoformat(), "number": part.number,
            "installment_count": entry.installment_count, "amount_cents": part.amount_cents,
            "scheduled_date": part.scheduled_date.isoformat(), "category_name": category_name,
            "responsible_id": entry.responsible_id, "responsible_name": responsible_name,
        } for part, entry, responsible_name, category_name in session.execute(statement)]
        settled = allocation_totals(session, [item["obligation_id"] for item in result["items"]])
        for item in result["items"]:
            item.update(settled[item["obligation_id"]])
        deltas = effective_amounts(session, [item["obligation_id"] for item in result["items"]])
        for item in result["items"]:
            item["original_amount_cents"] = item["amount_cents"]
            item["adjustment_cents"] = deltas[item["obligation_id"]]
            item["amount_cents"] += item["adjustment_cents"]
        from app.persistence.adjustments_models import InvoiceReconciliation, ObligationAdjustment
        from .adjustments import invoice_snapshot
        latest = session.scalar(select(InvoiceReconciliation).where(InvoiceReconciliation.invoice_id == invoice_id).order_by(InvoiceReconciliation.id.desc()))
        snapshot, _ = invoice_snapshot(session, invoice_id)
        from .settlements import canonical
        result["reconciliation"] = {"id":latest.id,"expected_total_cents":latest.expected_total_cents,"difference_cents":latest.expected_total_cents-result["statement_total_cents"],"current":latest.snapshot_json == canonical(snapshot),"reason":latest.reason,"recorded_at":latest.recorded_at} if latest else None
        result["adjustments"] = [{"id":a.id,"obligation_id":a.obligation_id,"delta_cents":a.delta_cents,"occurred_on":a.occurred_on.isoformat(),"reason":a.reason} for a in session.scalars(select(ObligationAdjustment).join(Obligation).where(Obligation.invoice_id == invoice_id).order_by(ObligationAdjustment.id).limit(100))]
        closure = session.scalar(select(InvoiceClosure).where(InvoiceClosure.invoice_id == invoice_id))
        result["closure"] = {"reason": closure.reason, "recorded_at": closure.recorded_at, "total_cents": closure.total_cents} if closure else None
        result.update(limit=limit, offset=offset)
        return result
