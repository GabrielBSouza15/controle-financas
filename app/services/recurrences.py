"""Regras mensais imutáveis; previsões consultadas não geram pagamentos."""
from datetime import date
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.domain.dates import add_months, date_in_month
from app.persistence.database import transaction
from app.persistence.models import RecurrenceSeries, RecurrenceChange, RecurrenceOccurrence, Entry, EntryChange, Card, CardRuleVersion, Invoice
from app.persistence.entries import EntryDraft, record_entry_in_session, PersistenceValidationError
from .entries import validate_references, get_entry
from .catalogs import NotFoundError, ConflictError
from .settlements import canonical, now_text, today_local, unused, replay

def series_or_fail(session, identity):
    row=session.get(RecurrenceSeries,identity)
    if row is None: raise NotFoundError("A recorrência não foi encontrada.")
    return row

def rule_at(session, series, month):
    amount=series.amount_cents; suspended=False
    for change in session.scalars(select(RecurrenceChange).where(RecurrenceChange.series_id==series.id,
            RecurrenceChange.effective_month<=month).order_by(RecurrenceChange.effective_month,RecurrenceChange.id)):
        if change.amount_cents is not None: amount=change.amount_cents
        if change.suspended is not None: suspended=change.suspended
    return amount,suspended

def occurrence_at(session, series, month):
    if month.day!=1: raise PersistenceValidationError("A referência deve ser o primeiro dia do mês.")
    scheduled=date_in_month(month.year,month.month,series.start_on.day)
    record=session.scalar(select(RecurrenceOccurrence).where(RecurrenceOccurrence.series_id==series.id,
        RecurrenceOccurrence.reference_month==month))
    if record:
        return {"series_id":series.id,"reference_month":month.isoformat(),"scheduled_on":record.scheduled_on.isoformat(),
            "amount_cents":record.expected_cents,"actual_cents":record.actual_cents,"status":record.status,"entry_id":record.entry_id,
            "reason":record.reason,"recorded_at":record.recorded_at}
    amount,suspended=rule_at(session,series,month)
    outside=scheduled<series.start_on or (series.end_on is not None and scheduled>series.end_on)
    return {"series_id":series.id,"reference_month":month.isoformat(),"scheduled_on":scheduled.isoformat(),
        "amount_cents":amount,"actual_cents":None,"status":"outside" if outside else "suspended" if suspended else "forecast","entry_id":None}

def series_data(session,row):
    result={c.name:getattr(row,c.name) for c in RecurrenceSeries.__table__.columns if c.name not in {"payload_json"}}
    result["current_amount_cents"],result["suspended"]=rule_at(session,row,today_local().replace(day=1))
    result["changes"]=[{c.name:getattr(change,c.name) for c in RecurrenceChange.__table__.columns if c.name!="payload_json"}
        for change in session.scalars(select(RecurrenceChange).where(RecurrenceChange.series_id==row.id).order_by(RecurrenceChange.effective_month,RecurrenceChange.id))]
    states = [change for change in result["changes"] if change["suspended"] is not None]
    latest = states[-1] if states else None
    result["listing_suspended"] = latest["suspended"] if latest else False
    result["listing_state_from"] = latest["effective_month"] if latest else None
    return result

def create_series(engine,data):
    document=canonical(data)
    with transaction(engine) as session:
        previous=session.scalar(select(RecurrenceSeries).where(RecurrenceSeries.request_key==data["request_key"]))
        if previous: replay(previous,document); identity=previous.id
        else:
            unused(session,data["request_key"])
            draft=EntryDraft(request_key=data["request_key"],description=data["description"],occurred_on=data["start_on"],
                total_cents=data["amount_cents"],installment_count=1,kind=data["kind"],payment_method=data["payment_method"],
                responsible_id=data["responsible_id"],category_id=data["category_id"],account_id=data["account_id"],card_id=data["card_id"])
            validate_references(session,draft)
            if data["end_on"] is not None and data["end_on"]<data["start_on"]:
                raise PersistenceValidationError("O fim não pode ser anterior à primeira previsão.")
            if data["card_id"] is not None:
                card=session.get(Card,data["card_id"])
                if card is None or not card.active: raise PersistenceValidationError("Selecione cartão ativo.")
                if session.scalar(select(CardRuleVersion.id).where(CardRuleVersion.card_id==card.id,
                        CardRuleVersion.effective_from<=data["start_on"])) is None:
                    raise PersistenceValidationError("Não há regra do cartão vigente na primeira previsão.")
            row=RecurrenceSeries(**data,recorded_at=now_text(),payload_json=document)
            session.add(row);session.flush();identity=row.id
    return get_series(engine,identity)

def get_series(engine,identity):
    with Session(engine) as session:return series_data(session,series_or_fail(session,identity))

def list_series(engine,limit=50,offset=0,visibility="all"):
    if visibility not in {"all", "active", "suspended"}:
        raise PersistenceValidationError("Escolha previsões ativas, suspensas ou todas.")
    with Session(engine) as session:
        rows=session.scalars(select(RecurrenceSeries).order_by(RecurrenceSeries.start_on,RecurrenceSeries.id))
        items=[series_data(session,row) for row in rows]
        if visibility != "all":
            items=[item for item in items if item["listing_suspended"] == (visibility == "suspended")]
        return {"items":items[offset:offset+limit],"total":len(items),"limit":limit,"offset":offset}


def change_series(engine,identity,data):
    document=canonical({"series_id":identity,**data})
    with transaction(engine) as session:
        previous=session.scalar(select(RecurrenceChange).where(RecurrenceChange.request_key==data["request_key"]))
        if previous:replay(previous,document)
        else:
            unused(session,data["request_key"]);series=series_or_fail(session,identity)
            if data["effective_month"]<series.start_on.replace(day=1):
                raise PersistenceValidationError("O início da alteração não pode preceder a recorrência.")
            if data["amount_cents"] is None and data["suspended"] is None:
                raise PersistenceValidationError("Informe reajuste ou mudança de suspensão.")
            session.add(RecurrenceChange(series_id=identity,**data,recorded_at=now_text(),payload_json=document))
    return get_series(engine,identity)

def occurrences(engine,identity,month_from,months):
    with Session(engine) as session:
        series=series_or_fail(session,identity)
        return {"items":[occurrence_at(session,series,add_months(month_from,i)) for i in range(months)],"series":series_data(session,series)}

def decide_occurrence(engine,identity,month,data):
    if data.get("history_confirmation") is None:data={k:v for k,v in data.items() if k!="history_confirmation"}
    if data.get("parents_repayment") is None:data={k:v for k,v in data.items() if k!="parents_repayment"}
    if data["action"] != "confirm" and data.get("parents_repayment") is not None:
        raise PersistenceValidationError("Dispensar previsão não informa recebimento de repasse.")
    if data["action"]!="confirm" and data.get("history_confirmation"):raise PersistenceValidationError("Dispensar não exige conferência de compra distinta.")
    document=canonical({"series_id":identity,"reference_month":month,**data})
    with transaction(engine) as session:
        previous=session.scalar(select(RecurrenceOccurrence).where(RecurrenceOccurrence.request_key==data["request_key"]))
        if previous:
            replay(previous,document);entry_id=previous.entry_id
        else:
            unused(session,data["request_key"]);series=series_or_fail(session,identity)
            current=occurrence_at(session,series,month)
            if current["status"]!="forecast":
                raise ConflictError("Esta ocorrência já foi decidida, está suspensa ou está fora do período da regra.")
            if current["amount_cents"]!=data["expected_cents"]:
                raise ConflictError("A previsão mudou. Atualize a ocorrência antes de confirmar.")
            entry_id=None
            if data["action"]=="confirm":
                actual_on=data["occurred_on"]
                if actual_on is None or actual_on.replace(day=1)!=month or actual_on>today_local():
                    raise PersistenceValidationError("Informe a data real, dentro do mês da ocorrência e até hoje.")
                if data["actual_cents"] is None:
                    raise PersistenceValidationError("Informe o valor efetivamente contratado/recebível.")
                draft=EntryDraft(request_key=data["request_key"],description=series.description,occurred_on=actual_on,
                    total_cents=data["actual_cents"],installment_count=1,kind=series.kind,payment_method=series.payment_method,
                    responsible_id=series.responsible_id,category_id=series.category_id,account_id=series.account_id,card_id=series.card_id)
                validate_references(session,draft)
                entry_id=record_entry_in_session(session,draft,data.get("history_confirmation"))
                from .parent_budget import register
                register(session,entry_id,data.get("parents_repayment"),created=True)
            elif data["actual_cents"] is not None or data["occurred_on"] is not None:
                raise PersistenceValidationError("Dispensar previsão não informa data/valor de realização.")
            row=RecurrenceOccurrence(request_key=data["request_key"],reason=data["reason"],series_id=identity,reference_month=month,
                scheduled_on=date.fromisoformat(current["scheduled_on"]),expected_cents=current["amount_cents"],
                actual_cents=data["actual_cents"],status="confirmed" if entry_id else "skipped",entry_id=entry_id,
                recorded_at=now_text(),payload_json=document)
            session.add(row);session.flush()
    return {"occurrence":occurrences(engine,identity,month,1)["items"][0],"entry":get_entry(engine,entry_id) if entry_id else None}
