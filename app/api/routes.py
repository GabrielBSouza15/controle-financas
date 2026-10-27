"""Rotas finas: contratos, casos de uso e respostas sem SQL na interface."""

from typing import Annotated, Literal
from fastapi import APIRouter, Path, Query, Request
from app.services import catalogs as catalog_service, entries as entry_service
from .schemas import AccountInput, CategoryInput, CivilInput, EntryInput, SetupInput, EditInput, CancelInput

router = APIRouter(prefix="/api")
QueryId = Annotated[int, Query(ge=1, le=entry_service.MAX_WEB_INTEGER)]


@router.get("/catalogs")
def catalogs(request: Request):
    return catalog_service.catalogs(request.app.state.engine)


@router.post("/setup")
def setup(payload: SetupInput, request: Request):
    return catalog_service.initialize_catalogs(request.app.state.engine, payload.effective_from)


@router.post("/accounts", status_code=201)
def account(payload: AccountInput, request: Request):
    return catalog_service.add_account(request.app.state.engine, payload.name)


@router.post("/categories", status_code=201)
def category(payload: CategoryInput, request: Request):
    return catalog_service.add_category(request.app.state.engine, payload.name, payload.parent_id)


@router.get("/entries")
def entries(request: Request, q: Annotated[str, Query(max_length=200)] = "",
            kind: Literal["expense", "income"] | None = None, responsible_id: QueryId | None = None,
            payment_method: Literal["credit", "pix", "cash", "pix_debit"] | None = None, card_id: QueryId | None = None,
            date_from: CivilInput | None = None, date_to: CivilInput | None = None,
            status: Literal["active", "cancelled", "superseded", "all"] = "active",
            limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0, le=1000000)] = 0):
    if date_from and date_to and date_from > date_to:
        from app.persistence.entries import PersistenceValidationError
        raise PersistenceValidationError("A data inicial deve ser anterior ou igual à final.")
    return entry_service.list_entries(request.app.state.engine, q=q, kind=kind, responsible_id=responsible_id,
        payment_method=payment_method, card_id=card_id, date_from=date_from, date_to=date_to, limit=limit, offset=offset, status=status)


@router.post("/entries")
def entry_create(payload: EntryInput, request: Request):
    return entry_service.create_entry(request.app.state.engine, payload.to_draft(), history_confirmation=payload.history_confirmation.model_dump() if payload.history_confirmation else None, parents_repayment=payload.parents_repayment, immediate_account_id=payload.immediate_account_id, paid_without_account=payload.paid_without_account)


@router.get("/entries/{entry_id}")
def entry_detail(request: Request, entry_id: Annotated[int, Path(ge=1, le=entry_service.MAX_WEB_INTEGER)]):
    return entry_service.get_entry(request.app.state.engine, entry_id)


@router.post("/entries/{entry_id}/edit")
def entry_edit(request: Request, entry_id: Annotated[int, Path(ge=1, le=entry_service.MAX_WEB_INTEGER)], payload: EditInput):
    return entry_service.change_entry(request.app.state.engine, entry_id, payload.request_key, payload.reason, payload.to_draft(), history_confirmation=payload.history_confirmation.model_dump() if payload.history_confirmation else None, parents_repayment=payload.parents_repayment)


@router.post("/entries/{entry_id}/cancel")
def entry_cancel(request: Request, entry_id: Annotated[int, Path(ge=1, le=entry_service.MAX_WEB_INTEGER)], payload: CancelInput):
    return entry_service.change_entry(request.app.state.engine, entry_id, payload.request_key, payload.reason)
