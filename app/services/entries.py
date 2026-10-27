"""Consulta e cadastro web; valores continuam calculados pelo domínio."""

from dataclasses import asdict
from datetime import datetime, timezone
import json
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.persistence.entries import EntryDraft, PersistenceValidationError, record_entry_in_session
from app.persistence.models import Account, Card, Category, Entry, EntryChange, Invoice, Obligation, Responsible, ObligationMove, EntryPaymentMark
from .catalogs import ConflictError, NotFoundError
from app.persistence.database import transaction
from app.persistence.queries import active_entry_filter
from app.persistence.settlements import allocation_totals, entry_has_settlement, financial_key_exists, effective_amounts

MAX_WEB_INTEGER = 2**53 - 1


def entry_data(session, entry, *, details=False):
    if entry.total_cents > MAX_WEB_INTEGER:
        raise PersistenceValidationError("Este valor excede a faixa exata suportada pela interface web.")
    responsible = session.get(Responsible, entry.responsible_id)
    category = session.get(Category, entry.category_id) if entry.category_id else None
    card = session.get(Card, entry.card_id) if entry.card_id else None
    account = session.get(Account, entry.account_id) if entry.account_id else None
    change = session.scalar(select(EntryChange).where(EntryChange.source_entry_id == entry.id))
    from .parent_budget import choices, budget_month
    repayment_status = choices(session).get(entry.id)
    result = {"status": "superseded" if change and change.action == "replace" else "cancelled" if change else "active",
              "replacement_id": change.replacement_entry_id if change else None, "id": entry.id, "description": entry.description, "kind": entry.kind,
              "total_cents": entry.total_cents, "installment_count": entry.installment_count,
              "occurred_on": entry.occurred_on.isoformat(), "payment_method": entry.payment_method,
              "responsible_id": entry.responsible_id, "responsible_name": responsible.name,
              "category_id": entry.category_id, "category_name": category.name if category else None,
              "card_id": entry.card_id, "card_name": card.name if card else None,
              "account_id": entry.account_id, "account_name": account.name if account else None,
              "paid_without_account": session.get(EntryPaymentMark, entry.id) is not None,
              "parents_repayment": repayment_status}
    if details:
        from app.persistence.models import HistoryEntryReview
        review = session.scalar(select(HistoryEntryReview).where(HistoryEntryReview.entry_id == entry.id))
        result["history_overlap_review"] = {"record_ids": json.loads(review.matches_json), "reason": review.reason, "recorded_at": review.recorded_at} if review else None
        result["has_settlement_links"] = entry_has_settlement(session, entry.id)
        result["can_modify"] = not change and not result["has_settlement_links"] and not session.scalar(select(Invoice.id).join(
            Obligation).where(
            Obligation.entry_id == entry.id, Invoice.status == "closed").limit(1))
        result["cycle_moves"] = [{"obligation_id":m.obligation_id,"source_invoice_id":m.source_invoice_id,"target_invoice_id":m.target_invoice_id,"reason":m.reason,"recorded_at":m.recorded_at} for m in session.scalars(select(ObligationMove).join(Obligation).where(Obligation.entry_id==entry.id).order_by(ObligationMove.id))]
        result["obligations"] = []
        paid = allocation_totals(session, [part.id for part in entry.obligations])
        deltas = effective_amounts(session, [part.id for part in entry.obligations])
        for part in entry.obligations:
            invoice = session.get(Invoice, part.invoice_id) if part.invoice_id else None
            result["obligations"].append({"obligation_id": part.id, **paid[part.id], "number": part.number, "original_amount_cents": part.amount_cents, "adjustment_cents": deltas[part.id], "amount_cents": part.amount_cents + deltas[part.id],
                "scheduled_date": part.scheduled_date.isoformat(), "budget_month": budget_month(part.scheduled_date, repayment_status).isoformat(), "invoice_id": part.invoice_id,
                "closing_date": invoice.closing_date.isoformat() if invoice else None,
                "due_date": invoice.due_date.isoformat() if invoice else None,
                "invoice_status": invoice.status if invoice else None})
    return result


def get_entry(engine, entry_id):
    with Session(engine) as session:
        entry = session.get(Entry, entry_id)
        if entry is None:
            raise NotFoundError("O lançamento não foi encontrado.")
        result = entry_data(session, entry, details=True)
        result["history"] = entry_history(session, entry)
        return result


def list_entries(engine, *, q="", kind=None, responsible_id=None, payment_method=None,
                 card_id=None, date_from=None, date_to=None, limit=50, offset=0, status="active"):
    conditions = []
    if status == "active":
        conditions.append(active_entry_filter())
    elif status in {"cancelled", "superseded"}:
        conditions.append(Entry.id.in_(select(EntryChange.source_entry_id).where(EntryChange.action == ("cancel" if status == "cancelled" else "replace"))))
    if q.strip():
        conditions.append(Entry.description.contains(q.strip(), autoescape=True))
    for column, value in [(Entry.kind, kind), (Entry.responsible_id, responsible_id),
                          (Entry.payment_method, None if payment_method == "pix_debit" else payment_method), (Entry.card_id, card_id)]:
        if value is not None:
            conditions.append(column == value)
    if date_from:
        conditions.append(Entry.occurred_on >= date_from)
    if date_to:
        conditions.append(Entry.occurred_on <= date_to)
    if payment_method == "pix_debit":
        conditions.append(Entry.payment_method.in_(("pix", "cash")))
    with Session(engine) as session:
        total = session.scalar(select(func.count()).select_from(Entry).where(*conditions))
        entries = session.scalars(select(Entry).where(*conditions).order_by(Entry.occurred_on, Entry.id).limit(limit).offset(offset))
        return {"items": [entry_data(session, entry) for entry in entries], "total": total, "limit": limit, "offset": offset}


def validate_references(session, draft):
    for model, identity, label in [(Responsible, draft.responsible_id, "responsável"),
                                   (Category, draft.category_id, "categoria"),
                                   (Account, draft.account_id, "conta")]:
        if identity is not None and session.get(model, identity) is None:
            raise NotFoundError(f"O cadastro de {label} não foi encontrado.")
    if draft.account_id is not None:
        account = session.get(Account, draft.account_id)
        if session.get(Responsible, account.owner_id).code != "self":
            raise PersistenceValidationError("Escolha uma conta sua; repasses e contas de terceiros têm tratamento próprio.")


def create_entry(engine, draft: EntryDraft, history_confirmation=None, parents_repayment=None, immediate_account_id=None, paid_without_account=False):
    with transaction(engine) as session:
        validate_references(session, draft)
        if session.scalar(select(EntryChange.id).where(EntryChange.request_key == draft.request_key)):
            raise ConflictError("Esta chave pertence a uma alteração, não a um novo lançamento.")
        created = session.scalar(select(Entry.id).where(Entry.request_key == draft.request_key)) is None
        identity = record_entry_in_session(session, draft, history_confirmation)
        from .parent_budget import register
        register(session, identity, parents_repayment, created=created)
        mark = session.get(EntryPaymentMark, identity)
        if type(paid_without_account) is not bool:
            raise PersistenceValidationError("A indicação de pagamento deve ser verdadeira ou falsa.")
        if not created and bool(mark) != paid_without_account:
            raise ConflictError("Preserve a indicação de pagamento da tentativa original.")
        if paid_without_account:
            from .settlements import today_local, now_text
            person = session.get(Responsible, draft.responsible_id)
            if (draft.kind != "expense" or draft.payment_method not in {"pix", "cash"}
                    or person.code != "self" or draft.account_id is not None or immediate_account_id is not None):
                raise PersistenceValidationError("Pago sem conta é exclusivo de despesa de Eu via PIX/Débito, sem conta vinculada.")
            if draft.occurred_on > today_local():
                raise PersistenceValidationError("Pagamento imediato não pode ter data futura. Use uma previsão.")
            if created:
                session.add(EntryPaymentMark(entry_id=identity, recorded_at=now_text()))
                session.flush()
        if immediate_account_id is not None:
            person = session.get(Responsible, draft.responsible_id)
            if draft.kind != "expense" or draft.payment_method not in {"pix", "cash"} or person.code != "self":
                raise PersistenceValidationError("Pagamento imediato é exclusivo de despesas de Eu via PIX/Débito.")
            from .settlements import record_event_in_session
            parts = session.scalars(select(Obligation).where(Obligation.entry_id == identity).order_by(Obligation.number)).all()
            for start in range(0, len(parts), 100):
                chunk=parts[start:start+100]
                record_event_in_session(session, dict(request_key="immediate:"+str(identity)+":"+str(start),
                    kind="expense_payment", occurred_on=draft.occurred_on, payer_id=draft.responsible_id,
                    account_id=immediate_account_id, invoice_id=None, reason="Despesa de Eu via PIX/Débito paga imediatamente",
                    amount_cents=sum(p.amount_cents for p in chunk),
                    allocations=[dict(obligation_id=p.id, amount_cents=p.amount_cents) for p in chunk]))
        elif not created:
            from app.persistence.models import FinancialEvent
            if session.scalar(select(FinancialEvent.id).where(FinancialEvent.request_key == "immediate:"+str(identity)+":0")):
                raise ConflictError("Esta tentativa já registrou pagamento imediato. Preserve a conta da tentativa original.")
    return get_entry(engine, identity)


def entry_history(session, entry):
    current = entry
    while True:
        previous = session.scalar(select(EntryChange).where(EntryChange.replacement_entry_id == current.id))
        if previous is None:
            break
        current = session.get(Entry, previous.source_entry_id)
    history = []
    while True:
        change = session.scalar(select(EntryChange).where(EntryChange.source_entry_id == current.id))
        row = entry_data(session, current)
        row.update(reason=change.reason if change else None, recorded_at=change.recorded_at if change else None)
        history.append(row)
        if not change or change.replacement_entry_id is None:
            return history
        current = session.get(Entry, change.replacement_entry_id)


def change_entry(engine, source_id, request_key, reason, draft=None, history_confirmation=None, parents_repayment=None):
    """Versão nova ou cancelamento, com chave única e transação compartilhada."""
    action = "replace" if draft else "cancel"
    document = {"source_id": source_id, "action": action, "reason": reason,
                "draft": asdict(draft) if draft else None}
    if history_confirmation is not None:
        document["history_confirmation"] = history_confirmation
    if parents_repayment is not None:
        document["parents_repayment"] = parents_repayment
    canonical = json.dumps(document, default=str, sort_keys=True, ensure_ascii=False)
    with transaction(engine) as session:
        previous = session.scalar(select(EntryChange).where(EntryChange.request_key == request_key))
        if previous is not None:
            if previous.payload_json != canonical:
                raise ConflictError("A chave já foi usada com outra alteração. Nenhum dado foi sobrescrito.")
            result_id = previous.replacement_entry_id or previous.source_entry_id
        else:
            if financial_key_exists(session, request_key):
                raise ConflictError("Esta chave pertence ao histórico financeiro.")
            if session.scalar(select(Entry.id).where(Entry.request_key == request_key)):
                raise ConflictError("A chave já pertence a outro lançamento.")
            source = session.get(Entry, source_id)
            if source is None:
                raise NotFoundError("O lançamento não foi encontrado.")
            if session.scalar(select(EntryChange.id).where(EntryChange.source_entry_id == source_id)):
                raise ConflictError("Este registro já foi alterado ou cancelado. Consulte sua versão atual antes de continuar.")
            if session.scalar(select(Invoice.id).join(Obligation).where(Obligation.entry_id == source_id, Invoice.status == "closed").limit(1)):
                raise ConflictError("Há parcela em fatura consolidada. A correção exige um ajuste específico; o histórico foi preservado.")
            if entry_has_settlement(session, source_id):
                raise ConflictError("Este compromisso possui vínculo de liquidação. Preserve o histórico e use uma correção específica.")
            target_id = None
            if draft:
                validate_references(session, draft)
                target_id = record_entry_in_session(session, draft, history_confirmation)
                from .parent_budget import register
                register(session, target_id, parents_repayment, created=True)
            session.add(EntryChange(request_key=request_key, source_entry_id=source_id,
                replacement_entry_id=target_id, action=action, reason=reason,
                recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), payload_json=canonical))
            session.flush()
            result_id = target_id or source_id
    return get_entry(engine, result_id)
