"""Escritas históricas delimitadas, com contratos estritos e segurança local."""
from typing import Annotated,Literal
from fastapi import APIRouter,Request
from pydantic import Field,model_validator
from .schemas import InputModel,PositiveId,CivilInput,Reason
from .settlements import Key,Money,OperationInput,PageLimit,PageOffset,Kinds
from app.services import history_events

class HistoryAllocationInput(InputModel):
 record_id:PositiveId
 amount_cents:Money

class HistoryEventInput(InputModel):
 request_key:Key
 kind:Kinds
 occurred_on:CivilInput
 amount_cents:Money
 payer_code:Literal["self","parents"]|None=None
 account_id:PositiveId|None=None
 reason:Reason
 allocations:Annotated[list[HistoryAllocationInput],Field(min_length=1,max_length=100)]
 @model_validator(mode="after")
 def validate_distribution(self):
  if sum(r.amount_cents for r in self.allocations)!=self.amount_cents:
   raise ValueError("Distribua exatamente o valor do fato real entre as parcelas.")
  return self

router=APIRouter(prefix="/api/history/events")

@router.get("")
def events(request:Request,limit:PageLimit=50,offset:PageOffset=0):
 return history_events.list_events(request.app.state.engine,limit,offset)

@router.post("")
def record(request:Request,payload:HistoryEventInput):
 return history_events.record_event(request.app.state.engine,payload.model_dump())

@router.get("/{identity}")
def detail(request:Request,identity:int):
 return history_events.get_event(request.app.state.engine,identity)

@router.post("/{identity}/reverse")
def reverse(request:Request,identity:int,payload:OperationInput):
 return history_events.reverse_event(request.app.state.engine,identity,payload.model_dump())
