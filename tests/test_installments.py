from datetime import date

import pytest

from app.domain import (
    CardRule, FinancialValidationError, MERCADO_PAGO, NUBANK,
    schedule_card_installments, schedule_monthly_installments,
)


def test_six_installments_are_assigned_to_successive_cycles():
    result = schedule_card_installments(120000, 6, date(2026, 10, 5), NUBANK)
    assert [part.number for part in result] == [1, 2, 3, 4, 5, 6]
    assert [part.amount_cents for part in result] == [20000] * 6
    assert [part.cycle.closing_date for part in result] == [
        date(2026, 11, 5), date(2026, 12, 5), date(2027, 1, 5),
        date(2027, 2, 5), date(2027, 3, 5), date(2027, 4, 5),
    ]
    assert sum(part.amount_cents for part in result) == 120000


def test_single_purchase_also_respects_card_closing_day():
    result = schedule_card_installments(100, 1, date(2026, 10, 11), MERCADO_PAGO)
    assert result[0].cycle.closing_date == date(2026, 10, 11)


def test_remainder_and_due_dates_are_preserved_through_card_schedule():
    result = schedule_card_installments(10000, 3, date(2026, 12, 11), MERCADO_PAGO)
    assert [part.amount_cents for part in result] == [3334, 3333, 3333]
    assert [part.cycle.due_date for part in result] == [date(2026, 12, 11), date(2027, 1, 11), date(2027, 2, 11)]


def test_short_closing_month_does_not_shift_all_following_cycles():
    rule = CardRule("Teste", 31, 31, True)
    result = schedule_card_installments(300, 3, date(2025, 1, 31), rule)
    assert [part.cycle.closing_date for part in result] == [date(2025, 1, 31), date(2025, 2, 28), date(2025, 3, 31)]


def test_pix_schedule_has_no_card_or_invoice():
    result = schedule_monthly_installments(10000, 3, date(2025, 1, 31))
    assert [part.number for part in result] == [1, 2, 3]
    assert [part.amount_cents for part in result] == [3334, 3333, 3333]
    assert [part.scheduled_date for part in result] == [date(2025, 1, 31), date(2025, 2, 28), date(2025, 3, 31)]
    assert not hasattr(result[0], "cycle")


def test_repeated_calculation_is_deterministic():
    first = schedule_card_installments(10000, 3, date(2026, 10, 5), NUBANK)
    assert schedule_card_installments(10000, 3, date(2026, 10, 5), NUBANK) == first


@pytest.mark.parametrize("total, count", [(0, 1), (2, 3), (100, True), (100.0, 2)])
def test_schedule_rejects_invalid_money_and_counts(total, count):
    with pytest.raises(FinancialValidationError):
        schedule_card_installments(total, count, date(2026, 10, 1), NUBANK)
    with pytest.raises(FinancialValidationError):
        schedule_monthly_installments(total, count, date(2026, 10, 1))


def test_failure_at_end_of_calendar_returns_no_partial_schedule():
    with pytest.raises(FinancialValidationError):
        schedule_card_installments(100, 2, date(9999, 12, 1), NUBANK)
    with pytest.raises(FinancialValidationError):
        schedule_monthly_installments(100, 2, date(9999, 12, 31))
