from datetime import date, datetime

import pytest

from app.domain import FinancialValidationError, add_months, monthly_dates
from app.domain.dates import date_in_month


@pytest.mark.parametrize("start, expected", [
    (date(2025, 1, 31), (date(2025, 1, 31), date(2025, 2, 28), date(2025, 3, 31), date(2025, 4, 30))),
    (date(2024, 1, 31), (date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 31), date(2024, 4, 30))),
    (date(2025, 1, 30), (date(2025, 1, 30), date(2025, 2, 28), date(2025, 3, 30), date(2025, 4, 30))),
    (date(2025, 1, 29), (date(2025, 1, 29), date(2025, 2, 28), date(2025, 3, 29), date(2025, 4, 29))),
])
def test_monthly_sequence_preserves_original_day(start, expected):
    assert monthly_dates(start, 4) == expected


def test_year_transition():
    assert monthly_dates(date(2026, 12, 31), 3) == (
        date(2026, 12, 31), date(2027, 1, 31), date(2027, 2, 28)
    )


def test_leap_day_returns_in_next_leap_year():
    result = monthly_dates(date(2024, 2, 29), 49)
    assert result[12] == date(2025, 2, 28)
    assert result[48] == date(2028, 2, 29)


def test_explicit_anchor_restores_original_day_after_clamping():
    february = add_months(date(2025, 1, 31), 1)
    assert add_months(february, 1, anchor_day=31) == date(2025, 3, 31)


@pytest.mark.parametrize("start", [None, "2026-10-17", datetime(2026, 10, 17), True])
def test_dates_must_be_civil_dates(start):
    with pytest.raises(FinancialValidationError):
        monthly_dates(start, 2)


@pytest.mark.parametrize("count", [0, -1, True, 1.5, "3"])
def test_invalid_sequence_counts(count):
    with pytest.raises(FinancialValidationError):
        monthly_dates(date(2026, 1, 1), count)


@pytest.mark.parametrize("months", [-1, True, 1.5])
def test_invalid_month_offset(months):
    with pytest.raises(FinancialValidationError):
        add_months(date(2026, 1, 1), months)


@pytest.mark.parametrize("year, month, day", [
    (0, 1, 1), (10000, 1, 1), (2026, 0, 1), (2026, 13, 1),
    (2026, 1, 0), (2026, 1, 32), (2026, 1, True),
])
def test_invalid_calendar_configuration(year, month, day):
    with pytest.raises(FinancialValidationError):
        date_in_month(year, month, day)


def test_calendar_upper_boundary_has_domain_error():
    assert monthly_dates(date(9999, 12, 31), 1) == (date(9999, 12, 31),)
    with pytest.raises(FinancialValidationError):
        monthly_dates(date(9999, 12, 31), 2)
