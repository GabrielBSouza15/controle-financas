"""Validações comuns, sem dependências de interface ou persistência."""

from datetime import date

from .errors import FinancialValidationError


def require_int(value: object, name: str, minimum: int, maximum: int | None = None) -> int:
    # bool é uma subclasse de int em Python, mas não é quantidade/dinheiro.
    if type(value) is not int or value < minimum:
        raise FinancialValidationError(f"{name} deve ser inteiro maior ou igual a {minimum}.")
    if maximum is not None and value > maximum:
        raise FinancialValidationError(f"{name} deve ser menor ou igual a {maximum}.")
    return value


def require_date(value: object) -> date:
    # datetime contém horário; não descartar horário/fuso implicitamente.
    if type(value) is not date:
        raise FinancialValidationError("Informe uma data civil (date), sem horário.")
    return value
