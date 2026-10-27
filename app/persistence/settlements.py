"""Consultas compartilhadas de confirmação, vínculos e proteção de chaves."""
from sqlalchemy import select
from .adjustments_models import (ObligationAdjustment, SettlementCorrection, AllocationOffset, CashTransfer, TransferReversal, InvoiceReconciliation)
from .models import (AllocationBatch, Entry, EntryChange, EventReversal, FinancialEvent,
                     InvoiceClosure, Obligation, Responsible, SettlementAllocation, RecurrenceSeries, RecurrenceChange, RecurrenceOccurrence, ObligationMove, InvoiceCreditApplication, InvoiceCreditAllocation, InvoiceCreditReversal)


def confirmed_event_filter():
    return select(AllocationBatch.id).where(AllocationBatch.event_id == FinancialEvent.id,
        AllocationBatch.request_key == FinancialEvent.request_key, AllocationBatch.posted.is_(True)).correlate(FinancialEvent).exists()


def active_event_filter():
    return confirmed_event_filter() & ~FinancialEvent.id.in_(select(EventReversal.event_id))


def financial_key_exists(session, key):
    from .models import HistoryEvent, HistoryEventReversal, HistoryOperation, PersonalOperation
    return any(session.scalar(select(model.id).where(model.request_key == key).limit(1)) is not None
        for model in (PersonalOperation, HistoryOperation, HistoryEvent, HistoryEventReversal, FinancialEvent, AllocationBatch, EventReversal, InvoiceClosure, ObligationAdjustment, SettlementCorrection, CashTransfer, TransferReversal, InvoiceReconciliation, RecurrenceSeries, RecurrenceChange, RecurrenceOccurrence, ObligationMove, InvoiceCreditApplication, InvoiceCreditReversal))


def operation_key_exists(session, key):
    return financial_key_exists(session, key) or any(session.scalar(select(model.id).where(model.request_key == key).limit(1)) is not None
        for model in (Entry, EntryChange))


def entry_has_settlement(session, identity):
    from .models import EntryPaymentMark
    if session.get(EntryPaymentMark, identity) is not None:
        return True
    return session.scalar(select(InvoiceCreditAllocation.id).join(Obligation, Obligation.id == InvoiceCreditAllocation.obligation_id).where(Obligation.entry_id == identity).limit(1)) is not None or session.scalar(select(ObligationAdjustment.id).join(Obligation, Obligation.id == ObligationAdjustment.obligation_id).where(Obligation.entry_id == identity).limit(1)) is not None or session.scalar(select(SettlementAllocation.id).join(Obligation,
        Obligation.id == SettlementAllocation.obligation_id).where(Obligation.entry_id == identity).limit(1)) is not None


def allocation_totals(session, obligation_ids):
    identities = list(obligation_ids)
    result = {identity: {"payment_cents": 0, "parents_covered_cents": 0, "credit_applied_cents": 0} for identity in identities}
    if not identities:
        return result
    statement = (select(SettlementAllocation.id, SettlementAllocation.obligation_id, SettlementAllocation.amount_cents,
                         FinancialEvent.kind, Responsible.code)
        .join(AllocationBatch, AllocationBatch.id == SettlementAllocation.batch_id)
        .join(FinancialEvent, FinancialEvent.id == AllocationBatch.event_id)
        .outerjoin(Responsible, Responsible.id == FinancialEvent.payer_id)
        .where(SettlementAllocation.obligation_id.in_(identities), AllocationBatch.posted.is_(True), active_event_filter()))
    rows = session.execute(statement).all()
    offsets = offset_totals(session, [row[0] for row in rows])
    for allocation_id, identity, amount, kind, code in rows:
        amount -= offsets[allocation_id]
        if kind != "repayment":
            result[identity]["payment_cents"] += amount
        if kind == "repayment" or code == "parents":
            result[identity]["parents_covered_cents"] += amount
    from .invoice_credits import obligation_credit_totals
    from .models import EntryPaymentMark
    for identity, amount in session.execute(select(Obligation.id, Obligation.amount_cents)
            .join(EntryPaymentMark, EntryPaymentMark.entry_id == Obligation.entry_id)
            .where(Obligation.id.in_(identities))):
        result[identity]["payment_cents"] += amount
    credits = obligation_credit_totals(session, identities)
    for identity in identities: result[identity]["credit_applied_cents"] = credits[identity]
    return result


def effective_amounts(session, identities):
    result = {identity: 0 for identity in identities}
    if result:
        for identity, amount in session.execute(select(ObligationAdjustment.obligation_id,
                ObligationAdjustment.delta_cents).where(ObligationAdjustment.obligation_id.in_(result))):
            result[identity] += amount
    return result

def offset_totals(session, allocation_ids):
    result = {identity: 0 for identity in allocation_ids}
    if result:
        for identity, amount in session.execute(select(AllocationOffset.allocation_id, AllocationOffset.amount_cents)
                .join(SettlementCorrection).where(AllocationOffset.allocation_id.in_(result), SettlementCorrection.posted.is_(True))):
            result[identity] += amount
    return result

def event_usage(session, identity):
    used = sum(session.scalars(select(AllocationBatch.amount_cents).where(
        AllocationBatch.event_id == identity, AllocationBatch.posted.is_(True))))
    refunded = 0
    for correction in session.scalars(select(SettlementCorrection).where(
            SettlementCorrection.event_id == identity, SettlementCorrection.posted.is_(True))):
        if correction.action == "release": used -= correction.amount_cents
        else:
            used += correction.unallocated_cents
            refunded += correction.amount_cents
    return used, refunded
