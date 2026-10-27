from dataclasses import FrozenInstanceError
from datetime import date, datetime

import pytest

from app.domain import CardRule, FinancialValidationError, MERCADO_PAGO, NUBANK, first_invoice_cycle
from app.domain.cards import cycle_for_month


@pytest.mark.parametrize("rule, purchase, closing", [
    (NUBANK, date(2026, 10, 4), date(2026, 10, 5)),
    (NUBANK, date(2026, 10, 5), date(2026, 11, 5)),
    (NUBANK, date(2026, 10, 6), date(2026, 11, 5)),
    (MERCADO_PAGO, date(2026, 10, 10), date(2026, 10, 11)),
    (MERCADO_PAGO, date(2026, 10, 11), date(2026, 10, 11)),
    (MERCADO_PAGO, date(2026, 10, 12), date(2026, 11, 11)),
    (NUBANK, date(2026, 12, 5), date(2027, 1, 5)),
    (MERCADO_PAGO, date(2026, 12, 12), date(2027, 1, 11)),
])
def test_approved_card_cutoffs(rule, purchase, closing):
    cycle = first_invoice_cycle(purchase, rule)
    assert cycle.closing_date == closing
    assert cycle.due_date == closing  # Datas aprovadas iguais, campos separados.


def test_cutoff_applies_to_the_effective_last_day_of_short_month():
    exclusive = CardRule("Teste", 31, 31, False)
    inclusive = CardRule("Teste", 31, 31, True)
    assert first_invoice_cycle(date(2025, 2, 28), exclusive).closing_date == date(2025, 3, 31)
    assert first_invoice_cycle(date(2025, 2, 28), inclusive).closing_date == date(2025, 2, 28)
    assert first_invoice_cycle(date(2024, 2, 28), exclusive).closing_date == date(2024, 2, 29)


def test_due_date_is_configurable_and_can_be_explicitly_next_month():
    rule = CardRule("Teste", 25, 5, True, due_month_offset=1)
    cycle = first_invoice_cycle(date(2026, 12, 24), rule)
    assert cycle.closing_date == date(2026, 12, 25)
    assert cycle.due_date == date(2027, 1, 5)
    later_due = CardRule("Teste", 5, 15, True)
    assert first_invoice_cycle(date(2026, 10, 4), later_due).due_date == date(2026, 10, 15)


@pytest.mark.parametrize("kwargs", [
    {"name": ""}, {"name": " "}, {"closing_day": 0}, {"closing_day": 32},
    {"closing_day": True}, {"due_day": 0}, {"due_day": 32},
    {"include_closing_day": 1}, {"due_month_offset": -1},
    {"due_month_offset": 2}, {"due_month_offset": True},
    {"closing_day": 15, "due_day": 5},
])
def test_invalid_card_configuration_is_rejected(kwargs):
    fields = dict(name="Teste", closing_day=5, due_day=5, include_closing_day=False)
    fields.update(kwargs)
    with pytest.raises(FinancialValidationError):
        CardRule(**fields)


def test_configuration_is_immutable():
    with pytest.raises(FrozenInstanceError):
        NUBANK.closing_day = 6


@pytest.mark.parametrize("rule", [None, "Nubank", 5])
def test_invalid_rule_is_rejected(rule):
    with pytest.raises(FinancialValidationError):
        first_invoice_cycle(date(2026, 10, 5), rule)


def test_timestamp_is_not_silently_converted():
    with pytest.raises(FinancialValidationError):
        first_invoice_cycle(datetime(2026, 10, 5), NUBANK)


def test_cycle_cannot_overflow_supported_dates():
    with pytest.raises(FinancialValidationError):
        first_invoice_cycle(date(9999, 12, 6), NUBANK)
    with pytest.raises(FinancialValidationError):
        cycle_for_month(CardRule("Teste", 25, 5, True, 1), date(9999, 12, 1))
