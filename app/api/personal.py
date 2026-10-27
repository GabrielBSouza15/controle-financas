"""Indicador independente, fluxo previsto/realizado e pagamento do ciclo completo."""
from typing import Annotated, Literal
from fastapi import APIRouter, Request, Query
from pydantic import Field, StrictInt
from sqlalchemy.orm import Session
from .schemas import InputModel, CivilInput, MonthInput, PositiveId
from .settlements import Key, Money
from app.services import personal
router=APIRouter(prefix="/api/personal")
class ReinvestmentInput(InputModel):
    request_key: Key
    mode: Literal["add","subtract","replace"]
    amount_cents: Annotated[StrictInt,Field(ge=0,le=2**53-1)]
    version: Annotated[StrictInt,Field(ge=0)]
class BillInput(InputModel):
    request_key: Annotated[str,Field(min_length=1,max_length=100)]
    card_id: PositiveId
    month: MonthInput
    occurred_on: CivilInput
    account_id: PositiveId
    expected_remaining_cents: Money
@router.get("/reinvestment")
def get_reinvestment(request:Request):
    with Session(request.app.state.engine) as session: return personal.reinvestment(session)
@router.post("/reinvestment")
def update_reinvestment(request:Request,payload:ReinvestmentInput):
    return personal.update_reinvestment(request.app.state.engine,payload.model_dump())
@router.get("/flow")
def get_flow(request:Request,date_from:CivilInput,date_to:CivilInput,limit:Annotated[int,Query(ge=1,le=100)]=50,offset:Annotated[int,Query(ge=0,le=1000000)]=0):
    return personal.flow(request.app.state.engine,date_from,date_to,limit,offset)
@router.get("/bill")
def get_bill(request:Request,card_id:Annotated[int,Query(ge=1)],month:MonthInput):
    with Session(request.app.state.engine) as session: return personal.bill_position(session,card_id,month)
@router.post("/bill")
def pay_bill(request:Request,payload:BillInput):
    return personal.pay_bill(request.app.state.engine,payload.model_dump())
