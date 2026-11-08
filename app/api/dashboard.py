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
            responsible_code: Literal["self", "parents", "all"] = "self",
            card_responsible_code: Literal["all", "self", "parents"] = "all"):
    return dashboard.summary(request.app.state.engine, month, months, chart_card_id, responsible_code, card_responsible_code)


@router.get("/details")
def details(
    request: Request, month: MonthInput,
    metric: Literal["self_expense", "self_income", "parents_expense", "self_result", "cash", "expense", "income", "result"],
    responsible_code: Literal["self", "parents", "all"] = "self",
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
    responsible_code: Literal["all", "self", "parents"] = "all",
    limit: PageLimit = 50, offset: PageOffset = 0,
):
    from app.services.dashboard_outlook import details
    return details(request.app.state.engine, month, metric, card_id=card_id, limit=limit, offset=offset, responsible_code=responsible_code)


@router.get("/expenses.pdf")
def expense_report(
    request: Request, month: MonthInput,
    responsible_code: Literal["self", "parents", "all"] = "self",
    category_id: Annotated[int, Query(ge=1)] | None = None,
    history_category: Annotated[str, Query(min_length=1, max_length=500)] | None = None,
    uncategorized: bool = False, other_categories: bool = False,
    category_name: Annotated[str, Query(min_length=1, max_length=500)] | None = None,
    phase: Literal["all", "registered", "forecast"] = "all",
):
    from fastapi.responses import Response
    from app.services.expense_pdf import report_data, render_pdf
    data = report_data(request.app.state.engine, month, responsible_code,
        category_id=category_id, history_category=history_category,
        uncategorized=uncategorized, other_categories=other_categories,
        category_name=category_name, phase=phase)
    scope = []
    if category_name is not None or history_category is not None:
        scope.append("Categoria: " + (category_name or history_category))
    elif category_id is not None:
        scope.append("Categoria: " + (data["items"][0]["category_name"] if data["items"] else "categoria selecionada"))
    elif uncategorized:
        scope.append("Sem categoria")
    elif other_categories:
        scope.append("Outras categorias do mês")
    if phase != "all":
        scope.append("Somente estimativas" if phase == "forecast" else "Somente valores já lançados")
    filename = f"despesas-{month:%Y-%m}-{responsible_code}.pdf"
    return Response(render_pdf(data, scope_label=" · ".join(scope)), media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })
