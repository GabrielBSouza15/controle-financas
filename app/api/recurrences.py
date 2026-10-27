from typing import Annotated, Literal
from fastapi import APIRouter, Request, Path, Query
from pydantic import Field, StrictBool, model_validator
from .schemas import CivilInput, MonthInput, PositiveId, Name, Reason, PaymentMethod
from .settlements import OperationInput, Money, PageLimit, PageOffset
from app.services import recurrences, projections
from .schemas import EntryInput, HistoryConfirmation
from pydantic import StringConstraints

class SeriesInput(OperationInput):
    description: Annotated[str,StringConstraints(strict=True,strip_whitespace=True,min_length=1,max_length=500)]
    kind: Literal["expense","income"]
    amount_cents: Money
    start_on: CivilInput
    end_on: CivilInput | None = None
    payment_method: PaymentMethod
    responsible_id: PositiveId
    category_id: PositiveId | None = None
    account_id: PositiveId | None = None
    card_id: PositiveId | None = None

class ChangeInput(OperationInput):
    effective_month: MonthInput
    amount_cents: Money | None = None
    suspended: StrictBool | None = None

class OccurrenceInput(OperationInput):
    parents_repayment: Literal["received", "pending"] | None = None
    history_confirmation: HistoryConfirmation | None = None
    action: Literal["confirm","skip"]
    expected_cents: Money
    actual_cents: Money | None = None
    occurred_on: CivilInput | None = None

router=APIRouter(prefix="/api")
@router.get("/recurrences")
def series_list(request:Request,limit:PageLimit=50,offset:PageOffset=0,visibility:Literal["all","active","suspended"]="all"):
    return recurrences.list_series(request.app.state.engine,limit,offset,visibility)

@router.post("/recurrences")
def series_create(request:Request,payload:SeriesInput):
    return recurrences.create_series(request.app.state.engine,payload.model_dump())

@router.get("/recurrences/{identity}")
def series_get(request:Request,identity:Annotated[int,Path(ge=1)]):
    return recurrences.get_series(request.app.state.engine,identity)

@router.post("/recurrences/{identity}/changes")
def series_change(request:Request,identity:Annotated[int,Path(ge=1)],payload:ChangeInput):
    return recurrences.change_series(request.app.state.engine,identity,payload.model_dump())

@router.get("/recurrences/{identity}/occurrences")
def occurrences(request:Request,identity:Annotated[int,Path(ge=1)],month_from:MonthInput,months:Annotated[int,Query(ge=1,le=120)]=12):
    return recurrences.occurrences(request.app.state.engine,identity,month_from,months)

@router.post("/recurrences/{identity}/occurrences/{month}")
def decide(request:Request,identity:Annotated[int,Path(ge=1)],month:MonthInput,payload:OccurrenceInput):
    return recurrences.decide_occurrence(request.app.state.engine,identity,month,payload.model_dump())

@router.get("/projections")
def projection(request:Request,month_from:MonthInput,months:Annotated[int,Query(ge=3,le=12)]=3,
        card_id:Annotated[int,Query(ge=1)]|None=None,responsible_id:Annotated[int,Query(ge=1)]|None=None,
        limit:PageLimit=100,offset:PageOffset=0):
    if months not in (3,6,12):
        from app.persistence.entries import PersistenceValidationError
        raise PersistenceValidationError("Escolha horizonte de 3, 6 ou 12 meses.")
    return projections.projection(request.app.state.engine,month_from,months,card_id=card_id,
        responsible_id=responsible_id,limit=limit,offset=offset)
