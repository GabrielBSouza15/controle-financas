"""Ciclos previstos de cartão; não representam pagamento ou conciliação."""

from dataclasses import dataclass
from datetime import date

from ._validation import require_date, require_int
from .dates import add_months, date_in_month
from .errors import FinancialValidationError


@dataclass(frozen=True, slots=True)
class CardRule:
    """Configuração explícita. Offset do vencimento: mesmo mês (0) ou seguinte (1)."""

    name: str
    closing_day: int
    due_day: int
    include_closing_day: bool
    due_month_offset: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise FinancialValidationError("Informe o nome do cartão.")
        require_int(self.closing_day, "Dia de fechamento", 1, 31)
        require_int(self.due_day, "Dia de vencimento", 1, 31)
        require_int(self.due_month_offset, "Mês relativo do vencimento", 0, 1)
        if type(self.include_closing_day) is not bool:
            raise FinancialValidationError("A inclusão do dia de fechamento deve ser booleana.")
        if self.due_month_offset == 0 and self.due_day < self.closing_day:
            raise FinancialValidationError("Vencimento anterior ao fechamento: informe o mês seguinte.")


@dataclass(frozen=True, slots=True)
class InvoiceCycle:
    """Datas previstas do ciclo. Não contém situação de pagamento."""

    closing_date: date
    due_date: date


# Configurações iniciais aprovadas; o cálculo aceita qualquer CardRule válida.
NUBANK = CardRule("Nubank", closing_day=5, due_day=5, include_closing_day=False)
MERCADO_PAGO = CardRule("Mercado Pago/Livre", closing_day=11, due_day=11, include_closing_day=True)


def cycle_for_month(rule: CardRule, reference: date) -> InvoiceCycle:
    """Obtém fechamento/vencimento do mês de referência."""
    if not isinstance(rule, CardRule):
        raise FinancialValidationError("Informe uma regra de cartão válida.")
    reference = require_date(reference)
    closing = date_in_month(reference.year, reference.month, rule.closing_day)
    due_reference = add_months(date(reference.year, reference.month, 1), rule.due_month_offset)
    due = date_in_month(due_reference.year, due_reference.month, rule.due_day)
    return InvoiceCycle(closing_date=closing, due_date=due)


def first_invoice_cycle(purchase_date: date, rule: CardRule) -> InvoiceCycle:
    """Aplica a inclusão/exclusão do próprio fechamento ao primeiro ciclo."""
    purchase_date = require_date(purchase_date)
    current = cycle_for_month(rule, purchase_date)
    if purchase_date < current.closing_date:
        return current
    if purchase_date == current.closing_date and rule.include_closing_day:
        return current
    next_month = add_months(date(purchase_date.year, purchase_date.month, 1), 1)
    return cycle_for_month(rule, next_month)
