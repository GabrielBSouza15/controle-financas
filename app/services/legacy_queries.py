"""Consulta somente leitura do ensaio e de sua origem, separada do caixa."""
import json
from sqlalchemy import func,select
from sqlalchemy.orm import Session
from app.persistence.models import LegacyImportBatch,LegacySourceRow
from app.persistence.entries import PersistenceValidationError
from .catalogs import NotFoundError
from .legacy_reader import normalized
from .settlements import safe

def batch_data(batch,*,details=False):
    control=json.loads(batch.control_json);safe(control["accepted_cents"])
    result={"id":batch.id,"file_sha256":batch.file_sha256,"original_name":batch.original_name,"policy_version":batch.policy_version,
        "recorded_at":batch.recorded_at,"source_rows":control["source_rows"],"counts":control["counts"],
        "accepted_cents":control["accepted_cents"],"reconciled":control["reconciled"],"issues":control["issues"],
        "purpose":"trial_only","operational_events_created":0}
    if details:result["control"]=control;result["context"]=json.loads(batch.context_json)
    return result

def list_batches(engine):
    with Session(engine) as session:
        return {"items":[batch_data(batch) for batch in session.scalars(select(LegacyImportBatch).order_by(LegacyImportBatch.id.desc()).limit(50))],
            "trial_only":True}

def get_batch(engine,identity):
    with Session(engine) as session:
        batch=session.get(LegacyImportBatch,identity)
        if batch is None:raise NotFoundError("Lote de ensaio não encontrado.")
        return batch_data(batch,details=True)

def row_data(source,*,detail=False):
    result={"id":source.id,"batch_id":source.batch_id,"sheet_name":source.sheet_name,"row_number":source.row_number,"status":source.status,
        "source_amount_decimal":source.source_amount_decimal,"issues":json.loads(source.issues_json),**json.loads(source.proposal_json)}
    if detail:result["raw"]=json.loads(source.raw_json)
    return result

def list_rows(engine,identity,*,status=None,q="",kind=None,responsible_code=None,card_code=None,month_from=None,month_to=None,limit=50,offset=0):
    if month_from and month_to and month_from>month_to:raise PersistenceValidationError("O mês inicial deve ser anterior ou igual ao final.")
    conditions=[LegacySourceRow.batch_id==identity]
    if status:conditions.append(LegacySourceRow.status==status)
    for key,value in [("kind",kind),("responsible_code",responsible_code),("card_code",card_code)]:
        if value:conditions.append(func.json_extract(LegacySourceRow.proposal_json,"$."+key)==value)
    if q.strip():conditions.append(func.json_extract(LegacySourceRow.proposal_json,"$.normalized_description").contains(normalized(q),autoescape=True))
    ref=func.substr(func.json_extract(LegacySourceRow.proposal_json,"$.reference_on"),1,7)
    if month_from:conditions.append(ref>=month_from.isoformat()[:7])
    if month_to:conditions.append(ref<=month_to.isoformat()[:7])
    with Session(engine) as session:
        if not session.get(LegacyImportBatch,identity):raise NotFoundError("Lote de ensaio não encontrado.")
        total=session.scalar(select(func.count()).select_from(LegacySourceRow).where(*conditions))
        rows=session.scalars(select(LegacySourceRow).where(*conditions).order_by(LegacySourceRow.row_number).limit(limit).offset(offset))
        return {"items":[row_data(row) for row in rows],"total":total,"limit":limit,"offset":offset,"trial_only":True}

def get_row(engine,batch_id,identity):
    with Session(engine) as session:
        row=session.scalar(select(LegacySourceRow).where(LegacySourceRow.id==identity,LegacySourceRow.batch_id==batch_id))
        if row is None:raise NotFoundError("Origem não encontrada neste lote.")
        return row_data(row,detail=True)
