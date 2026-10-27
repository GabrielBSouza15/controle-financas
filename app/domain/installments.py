"""Cronogramas previstos de parcelas; sem gravação ou eventos de caixa."""

from dataclasses import dataclass
from datetime import date

from .cards import CardRule, InvoiceCycle, cycle_for_month, first_invoice_cycle
from .dates import monthly_dates
from .money import split_installments


@dataclass(frozen=True, slots=True)
class CardInstallment:
    number: int
    amount_cents: int
    cycle: InvoiceCycle


@dataclass(frozen=True, slots=True)
class MonthlyInstallment:
    number: int
    amount_cents: int
    scheduled_date: date


def schedule_card_installments(
    total_cents: int, count: int, purchase_date: date, rule: CardRule
) -> tuple[CardInstallment, ...]:
    """Planeja parcelas em ciclos consecutivos, sem repetir a data da compra."""
    first = first_invoice_cycle(purchase_date, rule)
    references = monthly_dates(date(first.closing_date.year, first.closing_date.month, 1), count)
    amounts = split_installments(total_cents, count)
    return tuple(
        CardInstallment(number=index, amount_cents=amount, cycle=cycle_for_month(rule, reference))
        for index, (amount, reference) in enumerate(zip(amounts, references, strict=True), start=1)
    )


def schedule_monthly_installments(
    total_cents: int, count: int, first_date: date
) -> tuple[MonthlyInstallment, ...]:
    """Planeja obrigações mensais sem cartão, por exemplo um compromisso por PIX."""
    dates = monthly_dates(first_date, count)
    amounts = split_installments(total_cents, count)
    return tuple(
        MonthlyInstallment(number=index, amount_cents=amount, scheduled_date=reference)
        for index, (amount, reference) in enumerate(zip(amounts, dates, strict=True), start=1)
    )
