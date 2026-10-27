"""API de consulta: nenhuma confirmação de pagamento ou fechamento por data."""

from typing import Annotated, Literal
from fastapi import APIRouter, Path, Query, Request
from app.services import invoices as service
from app.services.entries import MAX_WEB_INTEGER
from .schemas import MonthInput
from .settlements import ClosureInput
from app.services.settlements import close_invoice

router = APIRouter(prefix="/api/invoices")
QueryId = Annotated[int, Query(ge=1, le=MAX_WEB_INTEGER)]
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=1000000)]


@router.get("")
def invoices(request: Request, card_id: QueryId | None = None, responsible_id: QueryId | None = None,
             month_from: MonthInput | None = None, month_to: MonthInput | None = None,
             status: Literal["open", "closed"] | None = None, limit: PageLimit = 50, offset: PageOffset = 0):
    return service.list_invoices(request.app.state.engine, card_id=card_id, responsible_id=responsible_id,
        month_from=month_from, month_to=month_to, status=status, limit=limit, offset=offset)


@router.get("/overview")
def overview(request: Request, card_id: QueryId | None = None, responsible_id: QueryId | None = None,
             month_from: MonthInput | None = None, month_to: MonthInput | None = None,
             status: Literal["open", "closed", "historical"] | None = None,
             limit: PageLimit = 50, offset: PageOffset = 0, include_forecasts: bool = False):
    from app.services.invoice_overview import list_overview
    return list_overview(request.app.state.engine, card_id=card_id, responsible_id=responsible_id,
                         month_from=month_from, month_to=month_to, status=status, limit=limit, offset=offset,
                         include_forecasts=include_forecasts)


@router.get("/overview/details")
def overview_details(request: Request, month: MonthInput, card_id: QueryId,
                     limit: PageLimit = 50, offset: PageOffset = 0, include_forecasts: bool = False):
    from app.services.invoice_overview import details
    return details(request.app.state.engine, month, card_id, limit=limit, offset=offset, include_forecasts=include_forecasts)


@router.get("/{invoice_id}")
def invoice(request: Request, invoice_id: Annotated[int, Path(ge=1, le=MAX_WEB_INTEGER)],
            limit: PageLimit = 50, offset: PageOffset = 0):
    return service.get_invoice(request.app.state.engine, invoice_id, limit=limit, offset=offset)


@router.post("/{invoice_id}/close")
def close(request: Request, invoice_id: Annotated[int, Path(ge=1, le=MAX_WEB_INTEGER)], payload: ClosureInput):
    return close_invoice(request.app.state.engine, invoice_id, payload.model_dump())
