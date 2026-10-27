"""Efeitos financeiros independentes, sem banco, HTTP ou números em float."""
from .errors import FinancialValidationError


def effects(kind, payer_code):
    if kind == "repayment" and payer_code == "parents":
        return ("parents",)
    if kind in {"invoice_payment", "expense_payment"} and payer_code in {"self", "parents"}:
        return ("payment", "parents") if payer_code == "parents" else ("payment",)
    if kind == "income_receipt" and payer_code is None:
        return ("payment",)
    raise FinancialValidationError("Tipo de registro ou pagador incompatível.")


def validate_capacity(amount, used, capacity):
    if any(type(value) is not int for value in (amount, used, capacity)):
        raise FinancialValidationError("Liquidação exige centavos inteiros.")
    if amount <= 0 or used < 0 or capacity < 0 or used + amount > capacity:
        raise FinancialValidationError("O valor aplicado excede o limite disponível da parcela.")


def cash_direction(kind, payer_code):
    effects(kind, payer_code)
    if kind in {"repayment", "income_receipt"}:
        return "in"
    return "out" if payer_code == "self" else "none"
