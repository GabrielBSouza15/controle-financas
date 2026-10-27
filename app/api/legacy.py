from typing import Annotated,Literal
from fastapi import APIRouter,Request,Path,Query
from .schemas import MonthInput
from .settlements import PageLimit,PageOffset
from app.services import legacy_queries as service

router=APIRouter(prefix="/api/legacy")
@router.get("/batches")
def batches(request:Request):
    return service.list_batches(request.app.state.engine)

@router.get("/batches/{identity}")
def batch(request:Request,identity:Annotated[int,Path(ge=1)]):
    return service.get_batch(request.app.state.engine,identity)

@router.get("/batches/{identity}/rows")
def rows(request:Request,identity:Annotated[int,Path(ge=1)],status:Literal["accepted","pending","excluded"]|None=None,
        q:Annotated[str,Query(max_length=200)]="",kind:Literal["income","expense"]|None=None,
        responsible_code:Literal["self","parents"]|None=None,card_code:Literal["nubank","mercado_pago"]|None=None,
        month_from:MonthInput|None=None,month_to:MonthInput|None=None,limit:PageLimit=50,offset:PageOffset=0):
    return service.list_rows(request.app.state.engine,identity,status=status,q=q,kind=kind,responsible_code=responsible_code,
        card_code=card_code,month_from=month_from,month_to=month_to,limit=limit,offset=offset)

@router.get("/batches/{batch_id}/rows/{identity}")
def row(request:Request,batch_id:Annotated[int,Path(ge=1)],identity:Annotated[int,Path(ge=1)]):
    return service.get_row(request.app.state.engine,batch_id,identity)
