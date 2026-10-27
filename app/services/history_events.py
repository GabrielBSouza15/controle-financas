"""Fatos novos de histórico: pagamentos e repasses nunca alteram a planilha importada."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.persistence.database import transaction
from app.persistence.models import HistoryRecord,HistoryBatch,HistoryEvent,HistoryAllocation,HistoryEventReversal
from app.persistence.entries import PersistenceValidationError
from app.domain.settlements import effects,cash_direction,validate_capacity
from .settlements import canonical,now_text,today_local,unused,replay,safe
from .adjustments import own_account
from .catalogs import ConflictError,NotFoundError

def active():
 return HistoryEvent.posted.is_(True)&~HistoryEvent.id.in_(select(HistoryEventReversal.event_id))

def totals(session):
 from .history_state import records_state,links
 result={identity:{"new_payment_cents":0,"known_parents_covered_cents":0,"effective_amount_cents":r["effective_cents"],"baseline_cents":r["baseline_cents"],"current_cycle":r["current_cycle"]} for identity,r in records_state(session).items()}
 for link in links(session):
  row=result[link["record_id"]];amount=link["remaining_cents"]
  if link["event_kind"]!="repayment":row["new_payment_cents"]+=amount
  if link["event_kind"]=="repayment" or link["payer_code"]=="parents":row["known_parents_covered_cents"]+=amount
 return result

def position(row,usage):
 values=usage.get(row.id,{"new_payment_cents":0,"known_parents_covered_cents":0})
 effective=values.get("effective_amount_cents",row.amount_cents)
 base=values.get("baseline_cents",row.amount_cents if row.paid_evidence else 0)
 paid=base+values["new_payment_cents"]
 pending=0 if row.role!="obligation" else max(0,effective-paid)
 state="not_applicable" if row.role!="obligation" else "zeroed" if effective==0 else "credit" if paid>effective else "pending" if pending and not paid else "partial" if pending else "paid_undated" if row.paid_evidence else "paid"
 return {**values,"effective_amount_cents":effective,"pending_cents":safe(pending),"payment_credit_cents":safe(max(0,paid-effective)),"payment_state":state,
  "parents_repayment_state":"unknown_history_with_records" if row.responsible_code=="parents" and values["known_parents_covered_cents"] else "unknown" if row.responsible_code=="parents" else "not_applicable"}

def record_event(engine,data):
 with transaction(engine) as session:
  identity=record_event_in_session(session,data)
 return get_event(engine,identity)

def record_event_in_session(session,data):
 document=canonical(data)
 old=session.scalar(select(HistoryEvent).where(HistoryEvent.request_key==data["request_key"]))
 if old:
  replay(old,document);identity=old.id
 else:
  unused(session,data["request_key"])
  dimensions=effects(data["kind"],data["payer_code"])
  direction=cash_direction(data["kind"],data["payer_code"])
  if data["occurred_on"]>today_local():raise PersistenceValidationError("Informe a data efetiva até hoje; previsão não é pagamento.")
  if direction=="none":
   if data["account_id"] is not None:raise PersistenceValidationError("Pagamento direto de Pai/Mãe não usa conta sua.")
  else:own_account(session,data["account_id"])
  allocations=data["allocations"]
  identities=[r["record_id"] for r in allocations]
  if not 1<=len(identities)<=100 or len(identities)!=len(set(identities)) or sum(r["amount_cents"] for r in allocations)!=data["amount_cents"]:
   raise PersistenceValidationError("Vincule exatamente o valor efetivo, sem repetir parcelas; limite de 100.")
  usage=totals(session);cycle=None
  for allocation in allocations:
   row=session.scalar(select(HistoryRecord).join(HistoryBatch).where(HistoryRecord.id==allocation["record_id"],HistoryBatch.posted.is_(True)))
   if not row or row.role!="obligation":raise PersistenceValidationError("Selecione uma obrigação histórica válida.")
   eligible=(data["kind"]=="invoice_payment" and row.kind=="expense" and row.card_code is not None
      or data["kind"]=="expense_payment" and row.kind=="expense" and row.card_code is None
      or data["kind"]=="income_receipt" and row.kind=="income" and row.responsible_code=="self" and row.card_code is None
      or data["kind"]=="repayment" and row.kind=="expense" and row.responsible_code=="parents")
   if not eligible or data["payer_code"]=="parents" and row.responsible_code!="parents":
    raise PersistenceValidationError("Tipo de registro ou pagador incompatível com a parcela.")
   if data["kind"]=="invoice_payment":
    key=(row.batch_id,row.card_code,usage[row.id]["current_cycle"])
    if cycle is not None and cycle!=key:raise PersistenceValidationError("Um pagamento ao banco deve corresponder a um único cartão e ciclo.")
    cycle=key
   pos=position(row,usage)
   for dimension in dimensions:
    used=pos["baseline_cents"]+pos["new_payment_cents"] if dimension=="payment" else pos["known_parents_covered_cents"]
    validate_capacity(allocation["amount_cents"],used,pos["effective_amount_cents"])
  from .history_state import cycle_payment,event_balances,links
  if data["kind"]=="invoice_payment" and cycle:
   bank=cycle_payment(session,*cycle)
   if data["amount_cents"]>bank["issuer_remaining_cents"]:
    raise ConflictError("O banco já recebeu esse valor. Reaplique o crédito sem vínculo ou registre a devolução real.")
  if data["kind"] in {"expense_payment","income_receipt"}:
   balances=event_balances(session)
   related={l["event_id"] for l in links(session) if l["record_id"] in identities and l["event_kind"]==data["kind"]}
   if any(balances[i]["available_cents"] for i in related):
    raise ConflictError("Há dinheiro desse fato sem vínculo. Reaplique antes de registrar outro pagamento.")
  values={k:v for k,v in data.items() if k!="allocations"}
  event=HistoryEvent(**values,recorded_at=now_text(),payload_json=document,posted=False)
  session.add(event);session.flush()
  for allocation in allocations:session.add(HistoryAllocation(event_id=event.id,**allocation))
  session.flush();event.posted=True;session.flush();identity=event.id
 return identity


def event_data(session,event):
 reversal=session.scalar(select(HistoryEventReversal).where(HistoryEventReversal.event_id==event.id))
 from .history_state import event_balances,links
 from app.persistence.models import HistoryOperation
 corrected=session.scalar(select(HistoryOperation.id).where(HistoryOperation.event_id==event.id).limit(1)) is not None
 balance=event_balances(session).get(event.id,{})
 active_links=[r for r in links(session) if r["event_id"]==event.id]
 return {"id":event.id,"kind":event.kind,"occurred_on":event.occurred_on,"amount_cents":event.amount_cents,
  "payer_code":event.payer_code,"account_id":event.account_id,"reason":event.reason,
  "status":"reversed" if reversal else "active","recorded_at":event.recorded_at,"balance":balance,"active_links":active_links,"has_corrections":corrected,
  "allocations":[{"record_id":a.record_id,"amount_cents":a.amount_cents} for a in session.scalars(select(HistoryAllocation).where(HistoryAllocation.event_id==event.id))]}

def get_event(engine,identity):
 with Session(engine) as session:
  event=session.scalar(select(HistoryEvent).where(HistoryEvent.id==identity,HistoryEvent.posted.is_(True)))
  if not event:raise NotFoundError("Registro histórico não encontrado.")
  return event_data(session,event)

def list_events(engine,limit=50,offset=0):
 with Session(engine) as session:
  rows=list(session.scalars(select(HistoryEvent).where(HistoryEvent.posted.is_(True)).order_by(HistoryEvent.occurred_on,HistoryEvent.id)))
  return {"items":[event_data(session,r) for r in rows[offset:offset+limit]],"total":len(rows),"limit":limit,"offset":offset}

def reverse_event(engine,identity,data):
 document=canonical({"event_id":identity,**data})
 with transaction(engine) as session:
  old=session.scalar(select(HistoryEventReversal).where(HistoryEventReversal.request_key==data["request_key"]))
  if old:replay(old,document)
  else:
   unused(session,data["request_key"])
   event=session.scalar(select(HistoryEvent).where(HistoryEvent.id==identity,active()))
   if not event:raise ConflictError("Registro inexistente ou já anulado.")
   from app.persistence.models import HistoryOperation
   if session.scalar(select(HistoryOperation.id).where(HistoryOperation.event_id==identity).limit(1)):
    raise ConflictError("Há correções nesse fato. Preserve a auditoria e use correção explícita.")
   session.add(HistoryEventReversal(event_id=identity,**data,recorded_at=now_text(),payload_json=document))
 return get_event(engine,identity)

def cash_records(session):
 rows=[]
 for event in session.scalars(select(HistoryEvent).where(active())):
  direction=cash_direction(event.kind,event.payer_code)
  if direction=="none":continue
  rows.append({"id":event.id,"kind":"history_"+event.kind,"source_kind":event.kind,
   "occurred_on":event.occurred_on.isoformat(),"amount_cents":event.amount_cents,
   "direction":direction,"account_id":event.account_id,"reason":event.reason})
 from .history_state import extra_cash
 rows.extend(extra_cash(session))
 return rows
