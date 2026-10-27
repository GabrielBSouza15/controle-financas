"""Erros de validação identificáveis pela futura camada de aplicação."""


class FinancialValidationError(ValueError):
    """Entrada incompatível com as regras do motor financeiro."""
