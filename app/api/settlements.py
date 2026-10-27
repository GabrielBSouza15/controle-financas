"""Contratos estritos para registros efetivos, aplicação de crédito e anulação."""
from typing import Annotated, Literal
from fastapi import APIRouter, Path, Query, Request
from pydantic import Field, StrictInt, StringConstraints, model_validator
from app.services import settlements as service
from .schemas import CivilInput, InputModel, PositiveId, Reason

Key = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=128)]
Money = Annotated[StrictInt, Field(ge=1, le=service.MAX_WEB_INTEGER)]
Kinds = Literal["invoice_payment", "repayment", "expense_payment", "income_receipt"]
QueryId = Annotated[int, Query(ge=1, le=service.MAX_WEB_INTEGER)]
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=1000000)]


class AllocationInput(InputModel):
    obligation_id: PositiveId
    amount_cents: Money


class EventInput(InputModel):
    request_key: Key
    kind: Kinds
    occurred_on: CivilInput
    amount_cents: Money
    payer_id: PositiveId | None = None
    account_id: PositiveId | None = None
    invoice_id: PositiveId | None = None
    reason: Reason
    allocations: Annotated[list[AllocationInput], Field(max_length=100)] = Field(default_factory=list)

    @model_validator(mode="after")
    def combination(self):
        if (self.kind == "income_receipt") != (self.payer_id is None):
            raise ValueError("Informe pagador para saídas/repasses; receita não usa pagador cadastrado.")
        if (self.kind == "invoice_payment") != (self.invoice_id is not None):
            raise ValueError("A referência de fatura é obrigatória e exclusiva do pagamento ao emissor.")
        total = sum(a.amount_cents for a in self.allocations)
        if total > self.amount_cents or (self.kind != "repayment" and total != self.amount_cents):
            raise ValueError("Confira o valor efetivo e sua distribuição entre as parcelas.")
        return self


class OperationInput(InputModel):
    request_key: Key
    reason: Reason


class CreditInput(OperationInput):
    allocations: Annotated[list[AllocationInput], Field(min_length=1, max_length=100)]


class ClosureInput(OperationInput):
    expected_total_cents: Annotated[StrictInt, Field(ge=0, le=service.MAX_WEB_INTEGER)]


router = APIRouter(prefix="/api/settlements")


@router.get("")
def events(request: Request, account_id: QueryId | None = None, kind: Kinds | None = None,
           date_from: CivilInput | None = None, date_to: CivilInput | None = None,
           status: Literal["active", "reversed", "all"] = "active", limit: PageLimit = 50, offset: PageOffset = 0):
    return service.list_events(request.app.state.engine, account_id=account_id, kind=kind, date_from=date_from,
        date_to=date_to, status=status, limit=limit, offset=offset)


@router.post("")
def event_create(request: Request, payload: EventInput):
    return service.record_event(request.app.state.engine, payload.model_dump())


@router.get("/obligations")
def available_obligations(request: Request, kind: Kinds, payer_id: QueryId | None = None,
        invoice_id: QueryId | None = None, entry_id: QueryId | None = None,
        q: Annotated[str, Query(max_length=200)] = "", limit: PageLimit = 50, offset: PageOffset = 0):
    return service.candidates(request.app.state.engine, kind=kind, payer_id=payer_id, invoice_id=invoice_id,
        entry_id=entry_id, q=q, limit=limit, offset=offset)


@router.get("/{event_id}")
def event_detail(request: Request, event_id: Annotated[int, Path(ge=1, le=service.MAX_WEB_INTEGER)],
                 limit: PageLimit = 50, offset: PageOffset = 0):
    return service.get_event(request.app.state.engine, event_id, limit=limit, offset=offset)


@router.post("/{event_id}/allocate")
def apply_credit(request: Request, event_id: Annotated[int, Path(ge=1, le=service.MAX_WEB_INTEGER)], payload: CreditInput):
    return service.allocate_credit(request.app.state.engine, event_id, payload.model_dump())


@router.post("/{event_id}/reverse")
def reverse(request: Request, event_id: Annotated[int, Path(ge=1, le=service.MAX_WEB_INTEGER)], payload: OperationInput):
    return service.reverse_event(request.app.state.engine, event_id, payload.model_dump())
