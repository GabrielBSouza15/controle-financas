"""Conversão exata de reais e distribuição de centavos."""

from decimal import Decimal
import re

from ._validation import require_int
from .errors import FinancialValidationError


_DECIMAL_TEXT = re.compile(r"[0-9]+(?:\.[0-9]+)?")


def reais_to_cents(value: str | Decimal) -> int:
    """Converte reais não negativos, sem arredondar ou aceitar float.

    Texto usa ponto decimal, sem moeda/separador de milhar. Zeros finais
    adicionais são permitidos, mas frações efetivas de centavo são rejeitadas.
    """
    if isinstance(value, str):
        text = value.strip()
        if _DECIMAL_TEXT.fullmatch(text) is None:
            raise FinancialValidationError("Use texto decimal não negativo, como '100.25'.")
        amount = Decimal(text)
    elif isinstance(value, Decimal):
        amount = value
    else:
        raise FinancialValidationError("Informe reais como texto ou Decimal; float não é aceito.")
    if not amount.is_finite() or amount < 0:
        raise FinancialValidationError("O valor deve ser finito e não negativo.")
    # Razão exata evita depender da precisão/arredondamento do contexto Decimal.
    numerator, denominator = amount.as_integer_ratio()
    cents, remainder = divmod(numerator * 100, denominator)
    if remainder:
        raise FinancialValidationError("O valor contém fração de centavo; confirme o valor correto.")
    return cents


def split_installments(total_cents: int, count: int) -> tuple[int, ...]:
    """Divide um total positivo em parcelas positivas, com resto nas primeiras."""
    total_cents = require_int(total_cents, "Total em centavos", 1)
    count = require_int(count, "Quantidade de parcelas", 1)
    if count > total_cents:
        raise FinancialValidationError("Cada parcela deve ter ao menos um centavo.")
    base, remainder = divmod(total_cents, count)
    return tuple(base + (1 if index < remainder else 0) for index in range(count))
