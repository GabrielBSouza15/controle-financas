"""Registro auditável de fatos confirmados e aplicação explícita às obrigações."""
from datetime import datetime, timedelta, timezone
import json
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.domain.settlements import effects, cash_direction, validate_capacity
from app.persistence.database import transaction
from app.persistence.entries import PersistenceValidationError
from app.persistence.models import (Account, AllocationBatch, Entry, EventReversal, FinancialEvent,
    Invoice, InvoiceClosure, Obligation, Responsible, SettlementAllocation)
from app.persistence.queries import active_entry_filter
from app.persistence.settlements import (active_event_filter, allocation_totals, confirmed_event_filter,
                                        operation_key_exists, effective_amounts, offset_totals, event_usage)
from .catalogs import ConflictError, NotFoundError

MAX_WEB_INTEGER = 2**53 - 1


def now_text():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_local():
    # Horário de Brasília atual, independente do fuso do processo do servidor.
    return datetime.now(timezone(timedelta(hours=-3))).date()


def canonical(data):
    return json.dumps(data, sort_keys=True, default=str, ensure_ascii=False)


def safe(value):
    if abs(value) > MAX_WEB_INTEGER:
        raise PersistenceValidationError("O valor agregado excede a faixa exata da interface web.")
    return value


def unused(session, key):
    if operation_key_exists(session, key):
        raise ConflictError("A chave já pertence a outra operação. Nenhum registro foi sobrescrito.")


def replay(record, document):
    if record.payload_json != document:
        raise ConflictError("A mesma chave foi enviada com dados diferentes.")


def payer_code(session, payer_id):
    if payer_id is None:
        return None
    person = session.get(Responsible, payer_id)
    if person is None:
        raise NotFoundError("O pagador não foi encontrado.")
    return person.code


def validate_event(session, data):
    code = payer_code(session, data["payer_id"])
    direction = cash_direction(data["kind"], code)
    if direction == "none":
        if data["account_id"] is not None:
            raise PersistenceValidationError("Pagamento direto de Pai/Mãe não usa conta sua.")
    else:
        account = session.get(Account, data["account_id"]) if data["account_id"] is not None else None
        if account is None or session.get(Responsible, account.owner_id).code != "self":
            raise PersistenceValidationError("Informe uma conta sua para a movimentação efetiva.")
    if data["occurred_on"] > today_local():
        raise PersistenceValidationError("A data efetiva não pode estar no futuro. Previsão não é liquidação.")
    if data["kind"] == "invoice_payment":
        if data["invoice_id"] is None or session.get(Invoice, data["invoice_id"]) is None:
            raise NotFoundError("Informe a fatura correspondente ao pagamento.")
    elif data["invoice_id"] is not None:
        raise PersistenceValidationError("Somente pagamento ao emissor usa a referência de fatura no registro.")
    return code


def validate_allocations(session, event, allocations):
    code = payer_code(session, event.payer_id)
    dimensions = effects(event.kind, code)
    identities = [row["obligation_id"] for row in allocations]
    if len(set(identities)) != len(identities):
        raise PersistenceValidationError("Uma parcela não pode se repetir no mesmo registro.")
    rows = session.execute(select(Obligation, Entry, Responsible.code).join(Entry,
        Entry.id == Obligation.entry_id).join(Responsible, Responsible.id == Entry.responsible_id)
        .where(Obligation.id.in_(identities), active_entry_filter())).all() if identities else []
    by_id = {part.id: (part, entry, owner) for part, entry, owner in rows}
    if len(by_id) != len(identities):
        raise ConflictError("Há parcela ausente, substituída ou cancelada. Atualize a consulta.")
    totals = allocation_totals(session, identities)
    deltas = effective_amounts(session, identities)
    for allocation in allocations:
        part, entry, owner = by_id[allocation["obligation_id"]]
        eligible = ((event.kind == "invoice_payment" and entry.kind == "expense" and part.invoice_id == event.invoice_id)
            or (event.kind == "repayment" and entry.kind == "expense" and owner == "parents")
            or (event.kind == "expense_payment" and entry.kind == "expense" and part.invoice_id is None)
            or (event.kind == "income_receipt" and entry.kind == "income" and part.invoice_id is None))
        if not eligible or (code == "parents" and owner != "parents"):
            raise PersistenceValidationError("A parcela não corresponde ao tipo de registro ou à responsabilidade do pagador.")
        if event.kind != "repayment" and event.occurred_on < entry.occurred_on:
            raise PersistenceValidationError("A data efetiva não pode preceder o lançamento de origem.")
        for dimension in dimensions:
            key = "payment_cents" if dimension == "payment" else "parents_covered_cents"
            validate_capacity(allocation["amount_cents"], totals[part.id][key] + (totals[part.id]["credit_applied_cents"] if dimension == "payment" else 0), part.amount_cents + deltas[part.id])


def add_batch(session, event, key, reason, allocations, document):
    validate_allocations(session, event, allocations)
    if key == event.request_key and event.kind == "invoice_payment":
        from .invoices import invoice_summaries
        invoice = session.get(Invoice, event.invoice_id)
        summary = invoice_summaries(session, [(invoice, "Cartão")])[invoice.id]
        if event.amount_cents > max(0, summary["issuer_remaining_cents"]):
            raise PersistenceValidationError("O valor excede a cobrança restante ao emissor. Confira pagamentos e créditos já registrados.")
    amount = sum(row["amount_cents"] for row in allocations)
    used, _ = event_usage(session, event.id)
    if amount + used > event.amount_cents:
        raise PersistenceValidationError("O valor aplicado excede o crédito disponível deste registro.")
    if key == event.request_key and event.kind != "repayment" and amount != event.amount_cents:
        raise PersistenceValidationError("O pagamento ou recebimento deve ser integralmente vinculado às parcelas.")
    batch = AllocationBatch(request_key=key, event_id=event.id, amount_cents=amount,
        reason=reason, recorded_at=now_text(), payload_json=document)
    session.add(batch); session.flush()
    for row in allocations:
        session.add(SettlementAllocation(batch_id=batch.id, **row)); session.flush()
    batch.posted = True; session.flush()
    return batch.id


def record_event(engine, data):
    with transaction(engine) as session:
        identity = record_event_in_session(session, data)
    return get_event(engine, identity)


def record_event_in_session(session, data):
    document = canonical(data)
    previous = session.scalar(select(FinancialEvent).where(FinancialEvent.request_key == data["request_key"]))
    if previous:
        replay(previous, document); identity = previous.id
    else:
        unused(session, data["request_key"])
        validate_event(session, data)
        values = {name: value for name, value in data.items() if name != "allocations"}
        event = FinancialEvent(**values, recorded_at=now_text(), payload_json=document)
        session.add(event); session.flush()
        add_batch(session, event, data["request_key"], data["reason"], data["allocations"], document)
        identity = event.id
    return identity


def allocate_credit(engine, identity, data):
    document = canonical({"event_id": identity, **data})
    with transaction(engine) as session:
        previous = session.scalar(select(AllocationBatch).where(AllocationBatch.request_key == data["request_key"]))
        if previous:
            replay(previous, document)
        else:
            unused(session, data["request_key"])
            event = session.scalar(select(FinancialEvent).where(FinancialEvent.id == identity, active_event_filter()))
            if event is None:
                raise NotFoundError("O registro ativo não foi encontrado.")
            if not data["allocations"]:
                raise PersistenceValidationError("Selecione ao menos uma parcela para aplicar o crédito.")
            add_batch(session, event, data["request_key"], data["reason"], data["allocations"], document)
    return get_event(engine, identity)


def reverse_event(engine, identity, data):
    document = canonical({"event_id": identity, **data})
    with transaction(engine) as session:
        previous = session.scalar(select(EventReversal).where(EventReversal.request_key == data["request_key"]))
        if previous:
            replay(previous, document)
        else:
            unused(session, data["request_key"])
            event = session.scalar(select(FinancialEvent).where(FinancialEvent.id == identity, active_event_filter()))
            if event is None:
                raise ConflictError("O registro não existe ou já foi anulado. Consulte o histórico.")
            from app.persistence.adjustments_models import SettlementCorrection
            if session.scalar(select(SettlementCorrection.id).where(SettlementCorrection.event_id == identity, SettlementCorrection.action == "refund", SettlementCorrection.posted.is_(True))):
                raise ConflictError("Há devolução real registrada; preserve o fato original e corrija por novo ajuste rastreável.")
            from app.persistence.models import InvoiceCreditApplication
            from app.persistence.invoice_credits import active_credit_filter
            if event.invoice_id and session.scalar(select(InvoiceCreditApplication.id).where(InvoiceCreditApplication.source_invoice_id == event.invoice_id, active_credit_filter())):
                raise ConflictError("O crédito desta fatura já foi compensado. Confira/corrija a compensação antes de anular a informação de pagamento.")
            session.add(EventReversal(event_id=identity, **data, recorded_at=now_text(), payload_json=document))
    return get_event(engine, identity)


def event_data(session, event):
    reversal = session.scalar(select(EventReversal).where(EventReversal.event_id == event.id))
    code = payer_code(session, event.payer_id)
    allocated, refunded = event_usage(session, event.id)
    account = session.get(Account, event.account_id) if event.account_id else None
    person = session.get(Responsible, event.payer_id) if event.payer_id else None
    return {"id": event.id, "kind": event.kind, "occurred_on": event.occurred_on.isoformat(),
        "amount_cents": event.amount_cents, "payer_id": event.payer_id, "payer_name": person.name if person else None,
        "account_id": event.account_id, "account_name": account.name if account else None,
        "invoice_id": event.invoice_id, "cash_direction": cash_direction(event.kind, code),
        "status": "reversed" if reversal else "active", "allocated_cents": allocated,
        "available_credit_cents": event.amount_cents - allocated if not reversal else 0, "refunded_cents": refunded,
        "reason": event.reason, "recorded_at": event.recorded_at,
        "reversal": {"reason": reversal.reason, "recorded_at": reversal.recorded_at} if reversal else None}


def get_event(engine, identity, *, limit=50, offset=0):
    with Session(engine) as session:
        event = session.scalar(select(FinancialEvent).where(FinancialEvent.id == identity, confirmed_event_filter()))
        if event is None:
            raise NotFoundError("O registro financeiro não foi encontrado.")
        result = event_data(session, event)
        conditions = [AllocationBatch.event_id == identity, AllocationBatch.posted.is_(True)]
        statement = (select(SettlementAllocation, AllocationBatch, Obligation, Entry)
            .join(AllocationBatch, AllocationBatch.id == SettlementAllocation.batch_id)
            .join(Obligation, Obligation.id == SettlementAllocation.obligation_id)
            .join(Entry, Entry.id == Obligation.entry_id).where(*conditions))
        count = session.scalar(select(func.count()).select_from(SettlementAllocation).join(AllocationBatch).where(*conditions))
        result["allocations"] = [{"allocation_id": allocation.id, "obligation_id": part.id, "entry_id": entry.id, "description": entry.description,
            "number": part.number, "installment_count": entry.installment_count, "amount_cents": allocation.amount_cents,
            "invoice_id": part.invoice_id, "batch_id": batch.id, "reason": batch.reason,
            "recorded_at": batch.recorded_at} for allocation, batch, part, entry in session.execute(
                statement.order_by(Obligation.scheduled_date, Entry.id, Obligation.number, SettlementAllocation.id).limit(limit).offset(offset))]
        offsets = offset_totals(session, [row["allocation_id"] for row in result["allocations"]])
        for row in result["allocations"]:
            row["active_cents"] = row["amount_cents"] - offsets[row["allocation_id"]]
        result.update(allocation_count=count, limit=limit, offset=offset)
        return result


def list_events(engine, *, account_id=None, kind=None, date_from=None, date_to=None,
                status="active", limit=50, offset=0):
    if date_from and date_to and date_from > date_to:
        raise PersistenceValidationError("A data inicial deve ser anterior ou igual à final.")
    conditions = [confirmed_event_filter()]
    if account_id: conditions.append(FinancialEvent.account_id == account_id)
    if kind: conditions.append(FinancialEvent.kind == kind)
    if date_from: conditions.append(FinancialEvent.occurred_on >= date_from)
    if date_to: conditions.append(FinancialEvent.occurred_on <= date_to)
    if status == "active": conditions.append(active_event_filter())
    if status == "reversed": conditions.append(FinancialEvent.id.in_(select(EventReversal.event_id)))
    with Session(engine) as session:
        total = session.scalar(select(func.count()).select_from(FinancialEvent).where(*conditions))
        events = session.scalars(select(FinancialEvent).where(*conditions).order_by(
            FinancialEvent.occurred_on, FinancialEvent.id).limit(limit).offset(offset))
        items = [event_data(session, event) for event in events]
        inflow = outflow = credit = 0
        for event in session.scalars(select(FinancialEvent).where(*conditions, active_event_filter())):
            data = event_data(session, event)
            if data["cash_direction"] == "in": inflow += event.amount_cents
            elif data["cash_direction"] == "out": outflow += event.amount_cents
            if event.kind == "repayment": credit += data["available_credit_cents"]
        from .adjustments import cash_extras
        extras = cash_extras(session, account_id=account_id, date_from=date_from, date_to=date_to) if status != "reversed" else []
        if kind: extras = [row for row in extras if row["source_kind"] == kind]
        inflow += sum(row["amount_cents"] for row in extras if row["direction"] == "in")
        outflow += sum(row["amount_cents"] for row in extras if row["direction"] == "out")
        return {"items": items, "total": total, "limit": limit, "offset": offset,
            "cash_in_cents": safe(inflow), "cash_out_cents": safe(outflow), "cash_variation_cents": safe(inflow-outflow),
            "available_credit_cents": safe(credit), "cash_extras": extras[offset:offset+limit], "cash_extra_count": len(extras)}


def candidates(engine, *, kind, payer_id=None, invoice_id=None, entry_id=None, q="", limit=50, offset=0):
    with Session(engine) as session:
        code = payer_code(session, payer_id); dimensions = effects(kind, code)
        if kind == "invoice_payment" and invoice_id is None:
            raise PersistenceValidationError("Selecione a fatura para consultar as parcelas.")
        conditions = [active_entry_filter()]
        if invoice_id: conditions.append(Obligation.invoice_id == invoice_id)
        if entry_id: conditions.append(Entry.id == entry_id)
        if q.strip(): conditions.append(Entry.description.contains(q.strip(),autoescape=True))
        if kind == "income_receipt": conditions.extend([Entry.kind == "income", Obligation.invoice_id.is_(None)])
        else: conditions.append(Entry.kind == "expense")
        if kind == "expense_payment": conditions.append(Obligation.invoice_id.is_(None))
        if code == "parents": conditions.append(Responsible.code == "parents")
        rows = session.execute(select(Obligation, Entry, Responsible.name).join(Entry,
            Entry.id == Obligation.entry_id).join(Responsible, Responsible.id == Entry.responsible_id)
            .where(*conditions).order_by(Obligation.scheduled_date, Entry.id, Obligation.number)).all()
        totals = allocation_totals(session, [part.id for part, _, _ in rows]); available = []
        deltas = effective_amounts(session, [part.id for part, _, _ in rows])
        for part, entry, person in rows:
            remaining = min(part.amount_cents + deltas[part.id] - (totals[part.id]["payment_cents"] + totals[part.id]["credit_applied_cents"] if dim == "payment" else totals[part.id]["parents_covered_cents"]) for dim in dimensions)
            if remaining > 0:
                available.append({"obligation_id": part.id, "entry_id": entry.id, "description": entry.description,
                    "number": part.number, "installment_count": entry.installment_count, "scheduled_date": part.scheduled_date.isoformat(),
                    "responsible_name": person, "amount_cents": safe(part.amount_cents + deltas[part.id]), "available_cents": safe(remaining), "invoice_id": part.invoice_id})
        return {"items": available[offset:offset+limit], "total": len(available), "limit": limit, "offset": offset}


def close_invoice(engine, identity, data):
    document = canonical({"invoice_id": identity, **data})
    with transaction(engine) as session:
        previous = session.scalar(select(InvoiceClosure).where(InvoiceClosure.request_key == data["request_key"]))
        if previous:
            replay(previous, document)
        else:
            unused(session, data["request_key"])
            invoice = session.get(Invoice, identity)
            if invoice is None: raise NotFoundError("A fatura não foi encontrada.")
            if invoice.status != "open": raise ConflictError("A fatura já está consolidada.")
            rows = session.execute(select(Obligation.id, Obligation.amount_cents, Entry.responsible_id)
                .join(Entry, Entry.id == Obligation.entry_id).where(Obligation.invoice_id == identity, active_entry_filter())
                .order_by(Obligation.id)).all()
            from .adjustments import invoice_snapshot
            snapshot, total = invoice_snapshot(session, identity)
            if total != data["expected_total_cents"]:
                raise ConflictError("O total da fatura mudou. Atualize e confira antes de fechar.")
            session.add(InvoiceClosure(request_key=data["request_key"], invoice_id=identity, total_cents=total,
                reason=data["reason"], recorded_at=now_text(), snapshot_json=canonical(snapshot), payload_json=document))
            session.flush()
    # Importação local evita dependência circular entre consultas e comandos.
    from .invoices import get_invoice
    return get_invoice(engine, identity)
