"""Correções sem reescrever origem, pagamento ou repasse."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.persistence.database import transaction
from app.persistence.models import HistoryOperation,HistoryRecord,HistoryEvent,HistoryAllocation,HistoryEventReversal
from app.persistence.entries import PersistenceValidationError
from .settlements import canonical,now_text,today_local,unused,replay
from .catalogs import ConflictError,NotFoundError
from .adjustments import own_account
from .history_state import records_state,links,event_balances

def operation_data(op):
    return {c.name:getattr(op,c.name) for c in HistoryOperation.__table__.columns if c.name not in {"payload_json","snapshot_json"}}

def snapshot(session,card_code,month):
    return canonical(sorted((r["id"],r["effective_cents"],r["current_cycle"].isoformat())
        for r in records_state(session).values() if r["role"]=="obligation" and r["card_code"]==card_code and r["current_cycle"]==month))

def record(engine,data):
    document=canonical(data)
    with transaction(engine) as session:
        old=session.scalar(select(HistoryOperation).where(HistoryOperation.request_key==data["request_key"]))
        if old:
            replay(old,document);return operation_data(old)
        unused(session,data["request_key"])
        if data["occurred_on"]>today_local():raise PersistenceValidationError("Informe a data até hoje; uma previsão não é fato realizado.")
        kind=data["kind"];states=records_state(session);row=states.get(data["record_id"])
        if kind not in {"reconcile","refund_credit"} and (not row or row["role"]!="obligation"):
            raise NotFoundError("Selecione uma obrigação histórica.")
        if data["account_id"] is not None:own_account(session,data["account_id"])
        if kind in {"refund","release"}:
            token=("a:"+str(data["allocation_id"])) if data["allocation_id"] else ("o:"+str(data["source_operation_id"]))
            link=next((r for r in links(session) if r["token"]==token),None)
            if not link or link["event_id"]!=data["event_id"] or link["record_id"]!=data["record_id"] or data["amount_cents"]>link["remaining_cents"]:
                raise ConflictError("Vínculo sem esse valor disponível. Atualize a consulta.")
        event=session.get(HistoryEvent,data["event_id"]) if data["event_id"] else None
        if data["event_id"] and not event:raise NotFoundError("Fato financeiro não encontrado.")
        if event and session.scalar(select(HistoryEventReversal.id).where(HistoryEventReversal.event_id==event.id)):
            raise ConflictError("O fato de origem foi anulado.")
        if kind in {"refund","refund_credit"} and (data["occurred_on"]<event.occurred_on or (event.account_id is None)!=(data["account_id"] is None)):
            raise PersistenceValidationError("Confira a data e a conta da devolução; pagamento externo não usa conta própria.")
        if kind in {"allocate","refund_credit"}:
            balance=event_balances(session).get(data["event_id"])
            if not balance or data["amount_cents"]>balance["available_cents"]:
                raise ConflictError("Crédito sem vínculo insuficiente.")
        values=dict(data)
        values["snapshot_json"]=snapshot(session,data["card_code"],data["target_month"]) if kind=="reconcile" else None
        if kind=="reconcile" and values["snapshot_json"]=="[]":
            raise PersistenceValidationError("Não há obrigações nesse cartão/ciclo.")
        op=HistoryOperation(**values,recorded_at=now_text(),payload_json=document)
        session.add(op);session.flush()
        return operation_data(op)

def overview(engine,limit=50,offset=0):
    with Session(engine) as session:
        rows=list(session.scalars(select(HistoryOperation).order_by(HistoryOperation.id.desc())))
        states=records_state(session);balances=event_balances(session)
        return {"items":[operation_data(r) for r in rows[offset:offset+limit]],"total":len(rows),
            "links":links(session),"events":balances,
            "event_options":[{"id":e.id,"kind":e.kind,"account_id":e.account_id,**balances[e.id]} for e in session.scalars(select(HistoryEvent).where(HistoryEvent.posted.is_(True))) if e.id in balances],
            "records":[{"id":r["id"],"source_row":r["source_row"],"description":r["description"],"kind":r["kind"],
                "responsible_code":r["responsible_code"],"card_code":r["card_code"],"effective_cents":r["effective_cents"]} for r in states.values() if r["role"]=="obligation"],
            "limit":limit,"offset":offset}

def reconciliation(session,card_code,month,total):
    op=session.scalar(select(HistoryOperation).where(HistoryOperation.kind=="reconcile",
        HistoryOperation.card_code==card_code,HistoryOperation.target_month==month).order_by(HistoryOperation.id.desc()))
    if not op:return {"state":"missing"}
    return {"state":"stale" if op.snapshot_json!=snapshot(session,card_code,month) else "matched" if op.amount_cents==total else "difference",
        "expected_total_cents":op.amount_cents,"difference_cents":op.amount_cents-total,"reason":op.reason,"recorded_at":op.recorded_at}
