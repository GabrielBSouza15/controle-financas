"""Créditos do emissor e pagamentos reais por fatura, sem fabricar caixa."""
from sqlalchemy import select
from .models import (InvoiceCreditApplication,InvoiceCreditAllocation,InvoiceCreditReversal,
    FinancialEvent,SettlementCorrection)
from .settlements import active_event_filter,event_usage

def active_credit_filter():
    return InvoiceCreditApplication.posted.is_(True) & ~InvoiceCreditApplication.id.in_(select(InvoiceCreditReversal.application_id))

def credit_totals(session,invoice_ids):
    result={identity:{"credit_in_cents":0,"credit_out_cents":0} for identity in invoice_ids}
    if not result:return result
    for row in session.scalars(select(InvoiceCreditApplication).where(active_credit_filter(),
            (InvoiceCreditApplication.source_invoice_id.in_(result)) | (InvoiceCreditApplication.target_invoice_id.in_(result)))):
        if row.source_invoice_id in result:result[row.source_invoice_id]["credit_out_cents"]+=row.amount_cents
        if row.target_invoice_id in result:result[row.target_invoice_id]["credit_in_cents"]+=row.amount_cents
    return result

def invoice_bank_totals(session,invoice_ids):
    result={identity:{"paid_cents":0,"unallocated_cents":0} for identity in invoice_ids}
    if not result:return result
    for event in session.scalars(select(FinancialEvent).where(
            FinancialEvent.kind=="invoice_payment",FinancialEvent.invoice_id.in_(result),active_event_filter())):
        used,refunded=event_usage(session,event.id)
        result[event.invoice_id]["paid_cents"]+=event.amount_cents-refunded
        result[event.invoice_id]["unallocated_cents"]+=event.amount_cents-used
    return result

def obligation_credit_totals(session,identities):
    result={identity:0 for identity in identities}
    if not result:return result
    for identity,amount in session.execute(select(InvoiceCreditAllocation.obligation_id,InvoiceCreditAllocation.amount_cents)
            .join(InvoiceCreditApplication).where(InvoiceCreditAllocation.obligation_id.in_(result),active_credit_filter())):
        result[identity]+=amount
    return result
