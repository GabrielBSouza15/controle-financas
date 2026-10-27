"""Entradas estritas para operações auditáveis da etapa 6C."""
from typing import Annotated, Literal
from fastapi import APIRouter, Request, Query, Path
from pydantic import Field, StrictInt, model_validator
from app.services import adjustments as service
from .schemas import CivilInput, PositiveId, MonthInput
from .settlements import OperationInput, Money, PageLimit, PageOffset

SignedMoney = Annotated[StrictInt, Field(ge=-(2**53-1), le=2**53-1)]

class AdjustmentInput(OperationInput):
    obligation_id: PositiveId
    delta_cents: SignedMoney
    occurred_on: CivilInput
    @model_validator(mode="after")
    def nonzero(self):
        if self.delta_cents == 0: raise ValueError("O ajuste precisa ter valor diferente de zero.")
        return self

from .schemas import InputModel
class Offset(InputModel):
    allocation_id: PositiveId
    amount_cents: Money

class CorrectionInput(OperationInput):
    event_id: PositiveId
    action: Literal["release","refund"]
    occurred_on: CivilInput
    amount_cents: Money
    unallocated_cents: Annotated[StrictInt, Field(ge=0, le=2**53-1)] = 0
    account_id: PositiveId | None = None
    offsets: Annotated[list[Offset], Field(max_length=100)] = Field(default_factory=list)

class TransferInput(OperationInput):
    source_account_id: PositiveId
    target_account_id: PositiveId
    occurred_on: CivilInput
    amount_cents: Money

class ReconciliationInput(OperationInput):
    expected_total_cents: SignedMoney

router=APIRouter(prefix="/api/adjustments")
@router.get("")
def overview(request:Request, limit:PageLimit=50, offset:PageOffset=0):
    return service.overview(request.app.state.engine,limit=limit,offset=offset)

@router.post("/obligations")
def adjustment(request:Request, payload:AdjustmentInput):
    return service.save_adjustment(request.app.state.engine,payload.model_dump())

@router.post("/corrections")
def correction(request:Request, payload:CorrectionInput):
    return service.correct_settlement(request.app.state.engine,payload.model_dump())

@router.post("/transfers")
def transfer(request:Request, payload:TransferInput):
    return service.save_transfer(request.app.state.engine,payload.model_dump())

@router.post("/transfers/{identity}/reverse")
def reverse(request:Request, identity:Annotated[int,Path(ge=1)], payload:OperationInput):
    return service.reverse_transfer(request.app.state.engine,identity,payload.model_dump())

@router.post("/invoices/{identity}/reconcile")
def reconcile(request:Request, identity:Annotated[int,Path(ge=1)], payload:ReconciliationInput):
    return service.reconcile_invoice(request.app.state.engine,identity,payload.model_dump())

class MoveInput(OperationInput):
    obligation_id: PositiveId
    expected_invoice_id: PositiveId
    target_month: MonthInput

@router.post("/cycles")
def move_cycle(request:Request,payload:MoveInput):
    from app.services.cycle_moves import move_obligation
    return move_obligation(request.app.state.engine,payload.model_dump())

from .settlements import AllocationInput
class InvoiceCreditInput(OperationInput):
    source_invoice_id: PositiveId
    target_invoice_id: PositiveId
    occurred_on: CivilInput
    amount_cents: Money
    allocations: Annotated[list[AllocationInput], Field(min_length=1, max_length=100)]

@router.post("/invoice-credits")
def invoice_credit(request:Request,payload:InvoiceCreditInput):
    from app.services.invoice_credits import record_credit
    return record_credit(request.app.state.engine,payload.model_dump())

@router.post("/invoice-credits/{identity}/reverse")
def reverse_invoice_credit(request:Request,identity:Annotated[int,Path(ge=1)],payload:OperationInput):
    from app.services.invoice_credits import reverse_credit
    return reverse_credit(request.app.state.engine,identity,payload.model_dump())
