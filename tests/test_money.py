from decimal import Decimal, localcontext

import pytest

from app.domain import FinancialValidationError, reais_to_cents, split_installments


@pytest.mark.parametrize("value, expected", [
    ("0", 0), ("0.01", 1), ("100.25", 10025), (" 1.20 ", 120),
    ("1.2300", 123), (Decimal("122.25"), 12225), (Decimal("0.10"), 10),
])
def test_reais_are_converted_exactly(value, expected):
    assert reais_to_cents(value) == expected


@pytest.mark.parametrize("value", [
    1.2, 100, True, None, "", "1,20", "1.000,00", "R$ 1.00", "1e2",
    "-1.00", "1_000", ".50", "1.001", Decimal("61.125"),
    Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-0.01"),
])
def test_invalid_or_ambiguous_money_is_rejected(value):
    with pytest.raises(FinancialValidationError):
        reais_to_cents(value)


def test_conversion_is_independent_of_decimal_context():
    with localcontext() as context:
        context.prec = 3
        assert reais_to_cents(Decimal("123456789012345678901234567890.12")) == 12345678901234567890123456789012
        with pytest.raises(FinancialValidationError):
            reais_to_cents(Decimal("61.125"))


@pytest.mark.parametrize("total, count, expected", [
    (120000, 6, (20000,) * 6), (10000, 3, (3334, 3333, 3333)),
    (12225, 2, (6113, 6112)), (5, 3, (2, 2, 1)), (1, 1, (1,)),
    (4, 4, (1, 1, 1, 1)),
])
def test_installments_preserve_total_and_put_extra_cents_first(total, count, expected):
    assert split_installments(total, count) == expected


def test_distribution_invariants_for_many_totals():
    for total in range(1, 201):
        for count in range(1, min(total, 24) + 1):
            amounts = split_installments(total, count)
            assert len(amounts) == count
            assert sum(amounts) == total
            assert min(amounts) >= 1
            assert max(amounts) - min(amounts) <= 1
            assert all(left >= right for left, right in zip(amounts, amounts[1:]))


@pytest.mark.parametrize("total, count", [
    (0, 1), (-1, 1), (100.0, 2), (True, 1), (100, 0), (100, -1),
    (100, 2.0), (100, True), (2, 3), ("100", 1), (100, None),
])
def test_invalid_installment_input_is_rejected(total, count):
    with pytest.raises(FinancialValidationError):
        split_installments(total, count)
