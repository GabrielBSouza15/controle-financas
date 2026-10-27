"""Consultas do painel: nenhuma operação de escrita."""
from typing import Annotated, Literal
from fastapi import APIRouter, Request, Query
from app.services import dashboard
from .schemas import MonthInput
from .settlements import PageLimit, PageOffset

router = APIRouter(prefix="/api/dashboard")


@router.get("")
def summary(request: Request, month: MonthInput, months: Annotated[int, Query(ge=3, le=12)] = 6,
            chart_card_id: Annotated[int, Query(ge=1)] | None = None,
            responsible_code: Literal["self", "parents"] = "self"):
    return dashboard.summary(request.app.state.engine, month, months, chart_card_id, responsible_code)


@router.get("/details")
def details(
    request: Request, month: MonthInput,
    metric: Literal["self_expense", "self_income", "parents_expense", "self_result", "cash", "expense", "income", "result"],
    responsible_code: Literal["self", "parents"] = "self",
    category_id: Annotated[int, Query(ge=1)] | None = None,
    history_category: Annotated[str, Query(min_length=1, max_length=500)] | None = None,
    uncategorized: bool = False,
    other_categories: bool = False,
    category_name: Annotated[str, Query(min_length=1, max_length=500)] | None = None,
    phase: Literal["all", "registered", "forecast"] = "all",
    limit: PageLimit = 50, offset: PageOffset = 0,
):
    return dashboard.details(request.app.state.engine, month, metric,
                             category_id=category_id, history_category=history_category, uncategorized=uncategorized, other_categories=other_categories, category_name=category_name,
                             phase=phase, limit=limit, offset=offset, responsible_code=responsible_code)


@router.get("/outlook/details")
def outlook_details(
    request: Request, month: MonthInput, metric: Literal["card", "all_cards", "card_expense", "all_expense", "all_income"],
    card_id: Annotated[int, Query(ge=1)] | None = None,
    limit: PageLimit = 50, offset: PageOffset = 0,
):
    from app.services.dashboard_outlook import details
    return details(request.app.state.engine, month, metric, card_id=card_id, limit=limit, offset=offset)
