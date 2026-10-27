"""Conciliação, alterações aditivas e dinheiro efetivamente devolvido."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.domain.settlements import cash_direction
from app.persistence.database import transaction
from app.persistence.entries import PersistenceValidationError
from app.persistence.models import Account, Entry, FinancialEvent, Invoice, Obligation, Responsible, AllocationBatch, SettlementAllocation, ObligationMove, InvoiceCreditApplication, InvoiceCreditReversal
from app.persistence.adjustments_models import (ObligationAdjustment, SettlementCorrection, AllocationOffset,
    CashTransfer, TransferReversal, InvoiceReconciliation)
from app.persistence.queries import active_entry_filter
from app.persistence.settlements import active_event_filter
from .catalogs import ConflictError, NotFoundError
from .settlements import canonical, now_text, today_local, safe, unused, replay, payer_code

def own_account(session, identity):
    account = session.get(Account, identity) if identity else None
    if account is None or session.get(Responsible, account.owner_id).code != "self":
        raise PersistenceValidationError("Informe uma conta sua para esta movimentação.")
    return account

from app.persistence.settlements import effective_amounts, offset_totals, event_usage

def save_adjustment(engine, data):
    document = canonical(data)
    with transaction(engine) as session:
        previous = session.scalar(select(ObligationAdjustment).where(ObligationAdjustment.request_key == data["request_key"]))
        if previous:
            replay(previous, document); identity = previous.id
        else:
            unused(session, data["request_key"])
            row = session.execute(select(Obligation, Entry).join(Entry).where(
                Obligation.id == data["obligation_id"], active_entry_filter())).first()
            if not row: raise NotFoundError("A parcela ativa não foi encontrada.")
            part, entry = row
            if entry.kind != "expense":
                raise PersistenceValidationError("Este ajuste é exclusivo de despesas.")
            if data["occurred_on"] < entry.occurred_on or data["occurred_on"] > today_local():
                raise PersistenceValidationError("A data do ajuste deve estar entre a origem e hoje.")
            current = part.amount_cents + effective_amounts(session, [part.id])[part.id]
            if not 0 <= current + data["delta_cents"] <= 2**53-1:
                raise PersistenceValidationError("O ajuste não pode tornar a parcela negativa ou exceder o limite monetário.")
            record = ObligationAdjustment(**data, recorded_at=now_text(), payload_json=document)
            session.add(record); session.flush(); identity = record.id
    return {"id": identity}

def correct_settlement(engine, data):
    document = canonical(data)
    with transaction(engine) as session:
        previous = session.scalar(select(SettlementCorrection).where(SettlementCorrection.request_key == data["request_key"]))
        if previous:
            replay(previous, document); identity = previous.id
        else:
            unused(session, data["request_key"])
            event = session.scalar(select(FinancialEvent).where(FinancialEvent.id == data["event_id"], active_event_filter()))
            if not event: raise ConflictError("Selecione um registro financeiro ativo.")
            if data["occurred_on"] > today_local() or (data["action"] == "refund" and data["occurred_on"] < event.occurred_on):
                raise PersistenceValidationError("A data da devolução deve estar entre o fato original e hoje.")
            direction = cash_direction(event.kind, payer_code(session, event.payer_id))
            if data["action"] == "refund" and direction != "none":
                own_account(session, data["account_id"])
            elif data["account_id"] is not None:
                raise PersistenceValidationError("Este registro não movimenta conta própria.")
            used, refunded = event_usage(session, event.id)
            if data["unallocated_cents"] and (data["action"] != "refund" or event.kind != "repayment"):
                raise PersistenceValidationError("Só repasse antecipado permite devolver crédito sem vínculo.")
            if data["unallocated_cents"] > event.amount_cents - used:
                raise PersistenceValidationError("Não há esse crédito sem vínculo disponível.")
            if data["action"] == "refund" and data["amount_cents"] > event.amount_cents - refunded:
                raise PersistenceValidationError("As devoluções excedem o valor do fato original.")
            identities = [a["allocation_id"] for a in data["offsets"]]
            if len(set(identities)) != len(identities):
                raise PersistenceValidationError("Não repita o mesmo vínculo na correção.")
            offsets = offset_totals(session, identities)
            for allocation in data["offsets"]:
                original = session.get(SettlementAllocation, allocation["allocation_id"])
                batch = session.get(AllocationBatch, original.batch_id) if original else None
                if not batch or not batch.posted or batch.event_id != event.id:
                    raise PersistenceValidationError("O vínculo não pertence ao registro financeiro selecionado.")
                if allocation["amount_cents"] > original.amount_cents - offsets[original.id]:
                    raise PersistenceValidationError("O valor excede a parte ainda ativa do vínculo.")
            if sum(x["amount_cents"] for x in data["offsets"]) + data["unallocated_cents"] != data["amount_cents"]:
                raise PersistenceValidationError("Distribua exatamente o valor entre os vínculos e o crédito sem vínculo.")
            values = {k:v for k,v in data.items() if k != "offsets"}
            record = SettlementCorrection(**values, recorded_at=now_text(), payload_json=document)
            session.add(record); session.flush()
            for allocation in data["offsets"]:
                session.add(AllocationOffset(correction_id=record.id, **allocation))
            session.flush(); record.posted = True; session.flush(); identity = record.id
    return {"id": identity}

def save_transfer(engine, data):
    document = canonical(data)
    with transaction(engine) as session:
        previous = session.scalar(select(CashTransfer).where(CashTransfer.request_key == data["request_key"]))
        if previous:
            replay(previous, document); identity = previous.id
        else:
            unused(session, data["request_key"])
            own_account(session, data["source_account_id"]); own_account(session, data["target_account_id"])
            if data["source_account_id"] == data["target_account_id"]:
                raise PersistenceValidationError("Origem e destino devem ser contas diferentes.")
            if data["occurred_on"] > today_local():
                raise PersistenceValidationError("Transferência prevista não é movimentação efetiva.")
            record = CashTransfer(**data, recorded_at=now_text(), payload_json=document)
            session.add(record); session.flush(); identity = record.id
    return {"id": identity}

def reverse_transfer(engine, identity, data):
    document = canonical({"transfer_id":identity, **data})
    with transaction(engine) as session:
        previous = session.scalar(select(TransferReversal).where(TransferReversal.request_key == data["request_key"]))
        if previous: replay(previous, document)
        else:
            unused(session, data["request_key"])
            if not session.get(CashTransfer, identity): raise NotFoundError("Transferência não encontrada.")
            if session.scalar(select(TransferReversal).where(TransferReversal.transfer_id == identity)):
                raise ConflictError("A transferência já foi anulada.")
            session.add(TransferReversal(transfer_id=identity, **data, recorded_at=now_text(), payload_json=document))
    return {"id": identity}

def invoice_snapshot(session, identity):
    rows = session.execute(select(Obligation.id, Obligation.amount_cents).join(Entry).where(
        Obligation.invoice_id == identity, active_entry_filter()).order_by(Obligation.id)).all()
    deltas = effective_amounts(session, [row.id for row in rows])
    adjustments = list(session.execute(select(ObligationAdjustment.id, ObligationAdjustment.delta_cents)
        .join(Obligation).where(Obligation.invoice_id == identity).order_by(ObligationAdjustment.id)))
    from app.persistence.invoice_credits import credit_totals, active_credit_filter
    credits = credit_totals(session, [identity])[identity]
    applications = session.execute(select(InvoiceCreditApplication.id, InvoiceCreditApplication.source_invoice_id, InvoiceCreditApplication.target_invoice_id, InvoiceCreditApplication.amount_cents).where((InvoiceCreditApplication.source_invoice_id == identity) | (InvoiceCreditApplication.target_invoice_id == identity), active_credit_filter()).order_by(InvoiceCreditApplication.id)).all()
    total = sum(row.amount_cents + deltas[row.id] for row in rows) + credits["credit_out_cents"] - credits["credit_in_cents"]
    return {"obligations":[list(row) for row in rows], "adjustments":[list(row) for row in adjustments], "credit_applications":[list(row) for row in applications]}, safe(total)

def reconcile_invoice(engine, identity, data):
    document = canonical({"invoice_id":identity, **data})
    with transaction(engine) as session:
        previous = session.scalar(select(InvoiceReconciliation).where(InvoiceReconciliation.request_key == data["request_key"]))
        if previous:
            replay(previous, document); record = previous
        else:
            unused(session, data["request_key"])
            if not session.get(Invoice, identity): raise NotFoundError("Fatura não encontrada.")
            snapshot, total = invoice_snapshot(session, identity)
            record = InvoiceReconciliation(invoice_id=identity, **data, calculated_total_cents=total,
                snapshot_json=canonical(snapshot), recorded_at=now_text(), payload_json=document)
            session.add(record); session.flush()
        return {"id":record.id, "calculated_total_cents":record.calculated_total_cents,
            "expected_total_cents":record.expected_total_cents, "difference_cents":safe(record.expected_total_cents-record.calculated_total_cents),
            "matches":record.expected_total_cents == record.calculated_total_cents}

def cash_extras(session, *, account_id=None, date_from=None, date_to=None):
    """Pernas de caixa; a transferência tem duas pernas e efeito consolidado zero."""
    items = []
    refunds = session.execute(select(SettlementCorrection, FinancialEvent).join(FinancialEvent).where(
        SettlementCorrection.posted.is_(True), SettlementCorrection.action == "refund", active_event_filter()))
    for correction, event in refunds:
        direction = cash_direction(event.kind, payer_code(session, event.payer_id))
        if direction != "none":
            items.append({"id":correction.id, "kind":"refund", "occurred_on":correction.occurred_on.isoformat(),
                "account_id":correction.account_id, "source_kind":event.kind, "amount_cents":correction.amount_cents,
                "direction":"out" if direction == "in" else "in", "reason":correction.reason})
    for transfer in session.scalars(select(CashTransfer).where(~CashTransfer.id.in_(select(TransferReversal.transfer_id)))):
        for account, direction in [(transfer.source_account_id,"out"),(transfer.target_account_id,"in")]:
            items.append({"id":transfer.id, "kind":"transfer", "occurred_on":transfer.occurred_on.isoformat(),
                "account_id":account, "source_kind":"transfer", "amount_cents":transfer.amount_cents, "direction":direction, "reason":transfer.reason})
    from .history_events import cash_records as history_cash_records
    items.extend(history_cash_records(session))
    return [row for row in items if (account_id is None or row["account_id"] == account_id)
        and (date_from is None or row["occurred_on"] >= date_from.isoformat())
        and (date_to is None or row["occurred_on"] <= date_to.isoformat())]

def overview(engine, *, limit=50, offset=0):
    with Session(engine) as session:
        items = []
        for model, kind in [(ObligationAdjustment,"adjustment"),(SettlementCorrection,"correction"),(CashTransfer,"transfer"),(InvoiceReconciliation,"reconciliation"),(ObligationMove,"cycle_move"),(InvoiceCreditApplication,"invoice_credit")]:
            total = session.scalars(select(model).order_by(model.id.desc()).limit(limit+offset+1))
            for row in total:
                if hasattr(row, "posted") and not row.posted: continue
                item = {column.name:getattr(row,column.name) for column in model.__table__.columns
                    if column.name not in {"payload_json","snapshot_json","posted"}}
                item["type"] = kind
                if isinstance(row,CashTransfer):
                    item["reversed"] = bool(session.scalar(select(TransferReversal.id).where(TransferReversal.transfer_id == row.id)))
                if isinstance(row, InvoiceCreditApplication):
                    item["reversed"] = bool(session.scalar(select(InvoiceCreditReversal.id).where(InvoiceCreditReversal.application_id == row.id)))
                items.append(item)
        items.sort(key=lambda row:(row["recorded_at"],row["type"],row["id"]), reverse=True)
        return {"items":items[offset:offset+limit], "limit":limit, "offset":offset,
            "has_more":len(items)>offset+limit, "cash_items":cash_extras(session)[-100:]}
