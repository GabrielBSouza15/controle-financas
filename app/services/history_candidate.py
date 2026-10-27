"""Importação atômica do candidato histórico e consultas sem efeitos de caixa."""
from datetime import date
from hashlib import sha256
import json
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.persistence.database import transaction
from app.persistence.models import HistoryAdoption, HistoryBatch, HistoryRecord, Entry, FinancialEvent, LegacyImportBatch
from app.persistence.entries import PersistenceValidationError
from .catalogs import ConflictError, NotFoundError
from .settlements import canonical, now_text, safe

POLICY="history-candidate-v1"

def integer(value, *, minimum=1, maximum=2**53-1):
    if type(value) is not int or not minimum<=value<=maximum:
        raise PersistenceValidationError("Valor inteiro histórico inválido.")
    return value

def import_history(engine, preview_bytes, confirmation_bytes):
    if len(preview_bytes)>32*1024*1024 or len(confirmation_bytes)>2*1024*1024:
        raise PersistenceValidationError("Documento histórico excede o limite.")
    try:
        preview=json.loads(preview_bytes)
        confirmation=json.loads(confirmation_bytes)
        source=preview["source_sha256"]
        if (not isinstance(source,str) or len(source)!=64 or any(c not in "0123456789abcdef" for c in source)
                or confirmation["source_sha256"]!=source
                or confirmation["preview_sha256"]!=sha256(preview_bytes).hexdigest()):
            raise ValueError
        rows=preview["rows"]
        if not isinstance(rows,list) or not rows:raise ValueError
        groups={}
        for group in confirmation["confirmed_groups"]:
            for number in group["source_rows"]:
                if number in groups:raise ValueError
                groups[number]=(group["id"],"confirmed")
        for index,group in enumerate(preview["sequence_candidates"]):
            for number in group["source_rows"]:
                if number in groups:raise ValueError
                groups[number]=(f"candidate-{index+1}","candidate")
        records=[]
        for row in rows:
            p=row["proposal"]; number=integer(row["row_number"],maximum=1048576)
            if row["blockers"] or row["status"] not in {"prepared","excluded"}:raise ValueError
            if type(p["paid_evidence"]) is not bool:raise ValueError
            role="excluded" if row["status"]=="excluded" else "opening_balance" if row["role"]=="opening_balance" else "obligation"
            if row["role"] not in {"opening_balance","observed_obligation"}:raise ValueError
            group_key,state=groups.get(number,(None,"unmapped"))
            record=dict(source_row=number,role=role,description=p["description"],kind=p["kind"],
                amount_cents=integer(p["amount_cents"]),reference_on=date.fromisoformat(p["reference_on"]),
                observed_number=integer(p["observed_number"]),observed_count=integer(p["observed_count"],maximum=100000),
                responsible_code=p["responsible_code"],payment_method=p["payment_method"],card_code=p["card_code"],
                simulated_cycle=date.fromisoformat(p["simulated_cycle"]) if p["simulated_cycle"] else None,
                category_original=p["category_original"],paid_evidence=p["paid_evidence"],
                group_key=group_key,group_state=state,evidence_json=canonical(row))
            if record["observed_number"]>record["observed_count"]:raise ValueError
            if role=="opening_balance":
                opening=row["opening_balance"]
                if (opening.get("confirmed_by_user") is not True or opening["amount_cents"]!=record["amount_cents"]
                        or opening["as_of"]!=p["reference_on"] or opening["bank_code"]!="nubank"):
                    raise ValueError
            records.append(record)
        identities={r["source_row"] for r in records}
        if len(identities)!=len(records) or not set(groups).issubset(identities):raise ValueError
        obligation=sum(r["amount_cents"] for r in records if r["role"]=="obligation")
        pending=sum(r["amount_cents"] for r in records if r["role"]=="obligation" and not r["paid_evidence"])
        safe(obligation);safe(pending)
        if preview["summary"]["payment_pending_cents"]!=pending:raise ValueError
    except (ValueError,KeyError,TypeError,AttributeError,UnicodeError):
        raise PersistenceValidationError("Prévia ou confirmações históricas inconsistentes.") from None
    digest=sha256(preview_bytes+b"\0"+confirmation_bytes).hexdigest()
    with transaction(engine) as session:
        previous=session.scalar(select(HistoryBatch).where(HistoryBatch.source_sha256==source))
        if previous:
            if previous.payload_sha256!=digest or not previous.posted:
                raise ConflictError("Esta origem já tem outra interpretação; preserve o candidato.")
            return {"id":previous.id,"created":False}
        if any(session.scalar(select(func.count()).select_from(model)) for model in (Entry,FinancialEvent,LegacyImportBatch,HistoryBatch)):
            raise ConflictError("O candidato exige base sem lançamentos, ensaio ou outro histórico.")
        batch=HistoryBatch(source_sha256=source,payload_sha256=digest,policy=POLICY,
            recorded_at=now_text(),record_count=len(records),obligation_cents=obligation,pending_cents=pending,
            decisions_json=canonical(confirmation),posted=False)
        session.add(batch);session.flush()
        for record in records:session.add(HistoryRecord(batch_id=batch.id,**record))
        session.flush();batch.posted=True;session.flush();identity=batch.id
    return {"id":identity,"created":True}

def history_rows(session):
    return list(session.scalars(select(HistoryRecord).join(HistoryBatch).where(HistoryBatch.posted.is_(True))
                               .order_by(HistoryRecord.source_row)))

def record_data(row, *, detail=False, usage=None):
    result={c.name:getattr(row,c.name) for c in HistoryRecord.__table__.columns if c.name!="evidence_json"}
    result["payment_state"]="not_applicable" if row.role!="obligation" else "paid_undated" if row.paid_evidence else "pending"
    result["paid_on"]=None
    result["parents_repayment_state"]="unknown" if row.responsible_code=="parents" else "not_applicable"
    result["contract_total_cents"]=None
    from .history_events import position
    result.update(position(row,usage or {}))
    if detail:
        result["evidence"]=json.loads(row.evidence_json)
        result["original_paid_on"]=None

    return result

def records(engine, *, pending_only=False, limit=50, offset=0, identity=None, q="", card_code=None, responsible_code=None, month=None):
    with Session(engine) as session:
        rows=history_rows(session)
        from .history_events import totals, position
        usage=totals(session)
        if identity is not None:
            row=next((r for r in rows if r.id==identity),None)
            if row is None:raise NotFoundError("Origem histórica não encontrada.")
            result=record_data(row,detail=True,usage=usage)
            from app.persistence.models import HistoryEvent,HistoryAllocation,HistoryEventReversal
            result["new_events"]=[{"id":event.id,"kind":event.kind,"occurred_on":event.occurred_on,
                "applied_cents":allocation.amount_cents,"reason":event.reason,
                "status":"reversed" if session.scalar(select(HistoryEventReversal.id).where(HistoryEventReversal.event_id==event.id)) else "active"}
                for event,allocation in session.execute(select(HistoryEvent,HistoryAllocation).join(HistoryAllocation)
                .where(HistoryAllocation.record_id==row.id,HistoryEvent.posted.is_(True)).order_by(HistoryEvent.occurred_on,HistoryEvent.id))]
            from app.persistence.models import HistoryOperation
            result["operations"]=[{c.name:getattr(o,c.name) for c in HistoryOperation.__table__.columns if c.name not in {"payload_json","snapshot_json"}} for o in session.scalars(select(HistoryOperation).where(HistoryOperation.record_id==row.id).order_by(HistoryOperation.id))]
            return result
        if pending_only:rows=[r for r in rows if r.role=="obligation" and position(r,usage)["pending_cents"]>0]
        if q.strip():rows=[r for r in rows if q.strip().casefold() in (r.description or "").casefold()]
        if card_code:rows=[r for r in rows if r.card_code==card_code]
        if responsible_code:rows=[r for r in rows if r.responsible_code==responsible_code]
        if month:rows=[r for r in rows if (usage[r.id]["current_cycle"] or r.reference_on.replace(day=1))==month]
        rows.sort(key=lambda r:(r.reference_on,r.source_row,r.id))
        return {"items":[record_data(r,usage=usage) for r in rows[offset:offset+limit]],
                "total":len(rows),"limit":limit,"offset":offset,"candidate_only":session.get(HistoryAdoption,1) is None}

def summary(engine):
    with Session(engine) as session:
        rows=history_rows(session);months={};invoices={};opening=[]
        from .history_events import totals,position,cash_records
        usage=totals(session);cash=cash_records(session)
        for row in rows:
            if row.role=="opening_balance":
                opening.append(record_data(row));continue
            if row.role!="obligation":continue
            effective=usage[row.id]["effective_amount_cents"]
            ref=(usage[row.id]["current_cycle"] or row.reference_on.replace(day=1)).isoformat()
            month=months.setdefault(ref,{"month":ref,"self_expense_cents":0,"parents_expense_cents":0,"self_income_cents":0})
            if row.kind=="expense":month[row.responsible_code+"_expense_cents"]+=effective
            elif row.responsible_code=="self":month["self_income_cents"]+=effective
            if row.card_code:
                key=(row.card_code,ref)
                invoice=invoices.setdefault(key,{"card_code":row.card_code,"month":ref,"total_cents":0,
                    "documented_paid_undated_cents":0,"new_payment_cents":0,"pending_cents":0,"parents_cents":0,
                    "parents_repayment_state":"unknown","cycle_state":"simulated_unreconciled"})
                invoice["total_cents"]+=effective
                pos=position(row,usage)
                if row.paid_evidence:invoice["documented_paid_undated_cents"]+=row.amount_cents
                invoice["new_payment_cents"]+=pos["new_payment_cents"]
                invoice["pending_cents"]+=pos["pending_cents"]
                if row.responsible_code=="parents":invoice["parents_cents"]+=effective
        from .history_state import cycle_payment
        from .history_operations import reconciliation
        for (code,ref),item in invoices.items():
            batch=next(r.batch_id for r in rows if r.card_code==code)
            item.update(cycle_payment(session,batch,code,date.fromisoformat(ref)))
            item["reconciliation"]=reconciliation(session,code,date.fromisoformat(ref),item["total_cents"])
        for item in list(months.values())+list(invoices.values()):
            for key,value in item.items():
                if key.endswith("_cents"):safe(value)
        return {"candidate_only":session.get(HistoryAdoption,1) is None,"source_rows":len(rows),
            "observed_obligations":sum(r.role=="obligation" for r in rows),
            "excluded_rows":sum(r.role=="excluded" for r in rows),
            "pending_rows":sum(r.role=="obligation" and position(r,usage)["pending_cents"]>0 for r in rows),
            "pending_cents":safe(sum(position(r,usage)["pending_cents"] for r in rows if r.role=="obligation")),
            "months":[months[k] for k in sorted(months)],
            "invoices":[invoices[k] for k in sorted(invoices)],
            "opening_balances":opening,"cash_events_created":0,"cash_in_cents":safe(sum(r["amount_cents"] for r in cash if r["direction"]=="in")),"cash_out_cents":safe(sum(r["amount_cents"] for r in cash if r["direction"]=="out")),
            "new_cash_event_count":len(cash),
            "cash_is_unreconstructed":True,"historical_budget_in_operational_dashboard":True,"new_effective_cash_in_operational_dashboard":True,
            "absolute_bank_balance_available":False}
