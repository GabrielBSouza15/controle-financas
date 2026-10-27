"""Consulta do histórico candidato; nenhuma adoção ou escrita HTTP."""
from fastapi import APIRouter, Request, Query
from typing import Annotated, Literal
from .schemas import MonthInput
from .settlements import PageLimit, PageOffset
from app.services import history_candidate
router=APIRouter(prefix="/api/history")

@router.get("/summary")
def summary(request:Request):
    return history_candidate.summary(request.app.state.engine)

@router.get("/records")
def records(request:Request,pending_only:bool=False,limit:PageLimit=50,offset:PageOffset=0, q:Annotated[str,Query(max_length=200)]="", card_code:Literal["nubank","mercado_pago"]|None=None, responsible_code:Literal["self","parents"]|None=None, month:MonthInput|None=None):
    return history_candidate.records(request.app.state.engine,pending_only=pending_only,limit=limit,offset=offset,q=q,card_code=card_code,responsible_code=responsible_code,month=month)

@router.get("/records/{identity}")
def record(request:Request,identity:int):
    return history_candidate.records(request.app.state.engine,identity=identity)
