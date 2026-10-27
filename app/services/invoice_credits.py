"""Compensação explícita de crédito real entre ciclos do mesmo cartão."""
from sqlalchemy import select
from app.persistence.database import transaction
from app.persistence.entries import PersistenceValidationError
from app.persistence.models import (Invoice,Entry,Obligation,InvoiceCreditApplication,InvoiceCreditAllocation,InvoiceCreditReversal,
    ObligationAdjustment,FinancialEvent,SettlementCorrection)
from app.persistence.queries import active_entry_filter
from app.persistence.settlements import allocation_totals,effective_amounts
from app.persistence.invoice_credits import active_credit_filter
from .settlements import canonical,now_text,today_local,unused,replay
from .catalogs import ConflictError,NotFoundError

def record_credit(engine,data):
    document=canonical(data)
    with transaction(engine) as session:
        previous=session.scalar(select(InvoiceCreditApplication).where(InvoiceCreditApplication.request_key==data["request_key"]))
        if previous:replay(previous,document);identity=previous.id
        else:
            unused(session,data["request_key"])
            source=session.get(Invoice,data["source_invoice_id"]);target=session.get(Invoice,data["target_invoice_id"])
            if not source or not target:raise NotFoundError("Fatura de origem/destino não encontrada.")
            if source.id==target.id or source.card_id!=target.card_id:
                raise PersistenceValidationError("O crédito só é compensado entre faturas diferentes do mesmo cartão.")
            if data["occurred_on"]>today_local():raise PersistenceValidationError("Informe a data real da compensação, até hoje.")
            from .invoices import invoice_summaries
            summaries=invoice_summaries(session,[(source,"Cartão"),(target,"Cartão")])
            if data["amount_cents"]>max(0,-summaries[source.id]["issuer_remaining_cents"]):
                raise ConflictError("O crédito disponível da origem mudou ou é insuficiente.")
            if data["amount_cents"]>max(0,summaries[target.id]["issuer_remaining_cents"]):
                raise ConflictError("O valor excede a cobrança restante do destino.")
            dates=list(session.scalars(select(ObligationAdjustment.occurred_on).join(Obligation).where(Obligation.invoice_id==source.id)))
            dates+=list(session.scalars(select(FinancialEvent.occurred_on).where(FinancialEvent.invoice_id==source.id)))
            dates+=list(session.scalars(select(InvoiceCreditApplication.occurred_on).where(
                InvoiceCreditApplication.target_invoice_id==source.id,active_credit_filter())))
            if dates and data["occurred_on"]<max(dates):
                raise PersistenceValidationError("A compensação não pode preceder os fatos que sustentam o crédito de origem.")
            ids=[row["obligation_id"] for row in data["allocations"]]
            if not ids or len(ids)!=len(set(ids)) or sum(row["amount_cents"] for row in data["allocations"])!=data["amount_cents"]:
                raise PersistenceValidationError("Distribua exatamente o crédito, sem repetir parcelas.")
            rows=session.execute(select(Obligation,Entry).join(Entry).where(Obligation.id.in_(ids),active_entry_filter())).all()
            by_id={part.id:(part,entry) for part,entry in rows};totals=allocation_totals(session,ids);deltas=effective_amounts(session,ids)
            for allocation in data["allocations"]:
                match=by_id.get(allocation["obligation_id"])
                if not match or match[0].invoice_id!=target.id or match[1].occurred_on>data["occurred_on"]:
                    raise PersistenceValidationError("Selecione parcelas da fatura de destino, já existentes na data da compensação.")
                part,_=match
                if allocation["amount_cents"]>part.amount_cents+deltas[part.id]-totals[part.id]["payment_cents"]-totals[part.id]["credit_applied_cents"]:
                    raise ConflictError("A parcela de destino já recebeu pagamento ou outro crédito.")
            values={key:value for key,value in data.items() if key!="allocations"}
            row=InvoiceCreditApplication(**values,recorded_at=now_text(),payload_json=document)
            session.add(row);session.flush()
            for allocation in data["allocations"]:session.add(InvoiceCreditAllocation(application_id=row.id,**allocation))
            session.flush();row.posted=True;session.flush();identity=row.id
    return {"id":identity}

def reverse_credit(engine,identity,data):
    document=canonical({"application_id":identity,**data})
    with transaction(engine) as session:
        previous=session.scalar(select(InvoiceCreditReversal).where(InvoiceCreditReversal.request_key==data["request_key"]))
        if previous:replay(previous,document)
        else:
            unused(session,data["request_key"])
            row=session.scalar(select(InvoiceCreditApplication).where(InvoiceCreditApplication.id==identity,active_credit_filter()))
            if row is None:raise ConflictError("Compensação ativa não encontrada.")
            session.add(InvoiceCreditReversal(application_id=identity,**data,recorded_at=now_text(),payload_json=document))
    return {"id":identity}
