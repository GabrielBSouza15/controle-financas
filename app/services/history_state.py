"""Posição histórica derivada de evidências e operações, em uma sessão."""
from datetime import date
from sqlalchemy import text, select
from app.persistence.models import HistoryOperation, HistoryEvent, HistoryEventReversal
from app.domain.settlements import cash_direction

def records_state(session):
    result={}
    for r in session.execute(text("SELECT * FROM history_current_records")).mappings():
        row=dict(r)
        row["current_cycle"]=date.fromisoformat(row["current_cycle"]) if row["current_cycle"] else None
        result[row["id"]]=row
    return result

def links(session):
    return [dict(r) for r in session.execute(text("SELECT * FROM history_current_links")).mappings()]

def event_balances(session):
    result={}
    refunded={}
    for op in session.scalars(select(HistoryOperation).where(HistoryOperation.kind.in_(("refund","refund_credit")))):
        refunded[op.event_id]=refunded.get(op.event_id,0)+op.amount_cents
    active=~HistoryEvent.id.in_(select(HistoryEventReversal.event_id))
    for e in session.scalars(select(HistoryEvent).where(HistoryEvent.posted.is_(True),active)):
        result[e.id]={"effective_cents":e.amount_cents-refunded.get(e.id,0),"allocated_cents":0,"refunded_cents":refunded.get(e.id,0)}
    for link in links(session):
        result[link["event_id"]]["allocated_cents"]+=link["remaining_cents"]
    for r in result.values():r["available_cents"]=r["effective_cents"]-r["allocated_cents"]
    return result

def cycle_payment(session, batch_id, card_code, cycle):
    current=session.execute(text("SELECT effective_cents,baseline_cents,new_paid_cents FROM history_cycle_positions WHERE batch_id=:batch AND card_code=:code AND current_cycle=:month"),{"batch":batch_id,"code":card_code,"month":cycle.isoformat()}).first()
    total,base,actual=current if current else (0,0,0)
    balances=event_balances(session)
    identities=session.scalars(text("SELECT DISTINCT e.id FROM history_events e JOIN history_allocations a ON a.event_id=e.id JOIN history_current_records r ON r.id=a.record_id WHERE e.kind='invoice_payment' AND r.batch_id=:batch AND r.card_code=:code AND r.current_cycle=:month"),{"batch":batch_id,"code":card_code,"month":cycle.isoformat()})
    available=sum(balances[i]["available_cents"] for i in identities if i in balances)
    return {"effective_cents":total,"issuer_paid_cents":base+actual,"issuer_remaining_cents":max(0,total-base-actual),"issuer_credit_cents":max(0,base+actual-total),"unallocated_cents":available}

def extra_cash(session):
    rows=[]
    for op in session.scalars(select(HistoryOperation).where(HistoryOperation.kind.in_(("refund","refund_credit","baseline_refund")))):
        if op.account_id is None:continue
        if op.kind in {"refund","refund_credit"}:
            e=session.get(HistoryEvent,op.event_id)
            direction="out" if cash_direction(e.kind,e.payer_code)=="in" else "in"
        else:
            record=records_state(session)[op.record_id]
            direction="in" if record["kind"]=="expense" else "out"
        rows.append({"id":op.id,"kind":"history_refund","source_kind":"refund","occurred_on":op.occurred_on.isoformat(),
                     "account_id":op.account_id,"amount_cents":op.amount_cents,"direction":direction,"reason":op.reason})
    return rows
