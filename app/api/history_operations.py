"""Contratos estritos de correção e conciliação histórica."""
from typing import Annotated,Literal
from fastapi import APIRouter,Request
from pydantic import Field,StrictInt,model_validator
from .schemas import PositiveId,CivilInput,MonthInput,Reason,InputModel
from .settlements import Key,PageLimit,PageOffset
from app.services import history_operations

class HistoryOperationInput(InputModel):
    request_key:Key
    kind:Literal["adjustment","refund","release","allocate","baseline_refund","refund_credit","move","reconcile"]
    record_id:PositiveId|None=None
    event_id:PositiveId|None=None
    allocation_id:PositiveId|None=None
    source_operation_id:PositiveId|None=None
    amount_cents:Annotated[StrictInt,Field(ge=-(2**53-1),le=2**53-1)]
    occurred_on:CivilInput
    account_id:PositiveId|None=None
    target_month:MonthInput|None=None
    card_code:Literal["nubank","mercado_pago"]|None=None
    reason:Reason
    @model_validator(mode="after")
    def shape(self):
        k=self.kind
        sources=(self.allocation_id is not None)+(self.source_operation_id is not None)
        if k=="reconcile":
            valid=self.record_id is None and self.card_code is not None and self.target_month is not None and self.amount_cents>=0 and self.event_id is None and sources==0 and self.account_id is None
        elif k=="refund_credit":
            valid=self.record_id is None and self.event_id is not None and self.amount_cents>0 and sources==0 and self.target_month is None and self.card_code is None
        elif k=="move":
            valid=self.record_id is not None and self.target_month is not None and self.amount_cents==0 and self.event_id is None and sources==0 and self.account_id is None and self.card_code is None
        elif k=="adjustment":
            valid=self.record_id is not None and self.amount_cents!=0 and self.event_id is None and sources==0 and self.account_id is None and self.target_month is None and self.card_code is None
        elif k=="baseline_refund":
            valid=self.record_id is not None and self.amount_cents>0 and self.event_id is None and sources==0 and self.account_id is not None and self.target_month is None and self.card_code is None
        else:
            valid=self.record_id is not None and self.event_id is not None and self.amount_cents>0 and self.target_month is None and self.card_code is None and (sources==0 if k=="allocate" else sources==1) and (k=="refund" or self.account_id is None)
        if not valid:raise ValueError("Campos incompatíveis com a operação selecionada.")
        return self

router=APIRouter(prefix="/api/history/operations")
@router.get("")
def overview(request:Request,limit:PageLimit=50,offset:PageOffset=0):
    return history_operations.overview(request.app.state.engine,limit,offset)
@router.post("")
def create(request:Request,payload:HistoryOperationInput):
    return history_operations.record(request.app.state.engine,payload.model_dump())
