"""Atribuição explícita de parcela aberta à fatura realmente cobrada."""
from sqlalchemy import select
from app.domain.cards import CardRule, cycle_for_month
from app.persistence.database import transaction
from app.persistence.models import ObligationMove, Obligation, Entry, Invoice, CardRuleVersion
from app.persistence.queries import active_entry_filter
from app.persistence.entries import PersistenceValidationError
from app.persistence.settlements import entry_has_settlement
from .catalogs import NotFoundError, ConflictError
from .settlements import canonical, now_text, unused, replay

def move_obligation(engine,data):
    document=canonical(data)
    with transaction(engine) as session:
        previous=session.scalar(select(ObligationMove).where(ObligationMove.request_key==data["request_key"]))
        if previous:replay(previous,document);identity=previous.id
        else:
            unused(session,data["request_key"])
            row=session.execute(select(Obligation,Entry).join(Entry).where(
                Obligation.id==data["obligation_id"],active_entry_filter())).first()
            if not row:raise NotFoundError("Parcela ativa não encontrada.")
            part,entry=row
            source=session.get(Invoice,part.invoice_id) if part.invoice_id else None
            if source is None:raise PersistenceValidationError("Somente parcela de cartão tem atribuição de ciclo.")
            if source.id!=data["expected_invoice_id"]:raise ConflictError("A parcela já mudou de ciclo. Atualize a consulta.")
            if source.status!="open" or entry_has_settlement(session,entry.id):
                raise ConflictError("Há consolidação, ajuste ou vínculo financeiro. Preserve o histórico e trate a diferença por ajuste.")
            rule=session.get(CardRuleVersion,entry.card_rule_id)
            cycle=cycle_for_month(CardRule("Cartão",rule.closing_day,rule.due_day,rule.include_closing_day,rule.due_month_offset),data["target_month"])
            if cycle.closing_date<entry.occurred_on:raise PersistenceValidationError("O ciclo não pode fechar antes da compra.")
            target=session.scalar(select(Invoice).where(Invoice.card_id==entry.card_id,Invoice.reference_month==data["target_month"]))
            if target is None:
                target=Invoice(card_id=entry.card_id,reference_month=data["target_month"],closing_date=cycle.closing_date,due_date=cycle.due_date,status="open")
                session.add(target);session.flush()
            if target.id==source.id:raise PersistenceValidationError("A parcela já está neste ciclo.")
            if target.status!="open" or (target.closing_date,target.due_date)!=(cycle.closing_date,cycle.due_date):
                raise ConflictError("A fatura de destino está consolidada ou possui datas incompatíveis.")
            record=ObligationMove(request_key=data["request_key"],reason=data["reason"],obligation_id=part.id,
                source_invoice_id=source.id,target_invoice_id=target.id,source_scheduled_on=part.scheduled_date,
                target_scheduled_on=target.due_date,recorded_at=now_text(),payload_json=document)
            session.add(record);session.flush();identity=record.id
    return {"id":identity,"obligation_id":data["obligation_id"]}
