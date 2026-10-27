"""Datas civis e deslocamentos por meses, preservando o dia de referência."""

from calendar import monthrange
from datetime import date

from ._validation import require_date, require_int
from .errors import FinancialValidationError


def date_in_month(year: int, month: int, anchor_day: int) -> date:
    """Usa o último dia do mês se o dia de referência não existir."""
    year = require_int(year, "Ano", 1, 9999)
    month = require_int(month, "Mês", 1, 12)
    anchor_day = require_int(anchor_day, "Dia de referência", 1, 31)
    return date(year, month, min(anchor_day, monthrange(year, month)[1]))


def add_months(start: date, months: int, *, anchor_day: int | None = None) -> date:
    """Avança meses sem usar blocos de 30 dias.

    Para encadear datas já ajustadas, informar explicitamente o dia original.
    monthly_dates faz isso automaticamente a partir da data inicial.
    """
    start = require_date(start)
    months = require_int(months, "Deslocamento em meses", 0)
    day = start.day if anchor_day is None else require_int(anchor_day, "Dia original", 1, 31)
    target = (start.year - 1) * 12 + start.month - 1 + months
    if target >= 9999 * 12:
        raise FinancialValidationError("A sequência ultrapassa o ano 9999.")
    year, month = divmod(target, 12)
    return date_in_month(year + 1, month + 1, day)


def monthly_dates(start: date, count: int) -> tuple[date, ...]:
    """Gera datas mensais incluindo start; não altera a data original."""
    start = require_date(start)
    count = require_int(count, "Quantidade de datas", 1)
    # Validar o último mês antes de montar uma sequência parcial/muito longa.
    add_months(start, count - 1)
    return tuple(add_months(start, index) for index in range(count))
