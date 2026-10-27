"""JSON com inteiros estritos; datas civis em ISO, sem coerções monetárias."""

from datetime import date
import re
from typing import Annotated, Literal
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictInt, StringConstraints, model_validator
from app.persistence.entries import EntryDraft
from app.services.entries import MAX_WEB_INTEGER


def civil_date(value):
    if type(value) is date:
        return value
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("Informe uma data no formato AAAA-MM-DD, sem horário.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("Informe uma data civil válida.") from None


CivilInput = Annotated[date, BeforeValidator(civil_date)]
PositiveId = Annotated[StrictInt, Field(ge=1, le=MAX_WEB_INTEGER)]
Name = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetupInput(InputModel):
    effective_from: CivilInput


class AccountInput(InputModel):
    name: Name


class CategoryInput(InputModel):
    name: Name
    parent_id: PositiveId | None = None


class HistoryConfirmation(InputModel):
    record_ids: Annotated[list[PositiveId], Field(min_length=1, max_length=1447)]
    reason: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=1000)]


def normalize_payment_method(value):
    return "pix" if value == "pix_debit" else value


PaymentMethod = Annotated[Literal["credit", "pix", "cash"], BeforeValidator(normalize_payment_method)]


class EntryInput(InputModel):
    paid_without_account: StrictBool = False
    immediate_account_id: PositiveId | None = None
    parents_repayment: Literal["received", "pending"] | None = None
    history_confirmation: HistoryConfirmation | None = None
    request_key: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128)]
    description: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=500)]
    occurred_on: CivilInput
    total_cents: Annotated[StrictInt, Field(ge=1, le=MAX_WEB_INTEGER)]
    installment_count: Annotated[StrictInt, Field(ge=1, le=1200)]
    responsible_id: PositiveId
    payment_method: PaymentMethod
    kind: Literal["expense", "income"]
    category_id: PositiveId | None = None
    account_id: PositiveId | None = None
    card_id: PositiveId | None = None

    @model_validator(mode="after")
    def validate_combination(self):
        EntryDraft(**{key: value for key, value in self.model_dump().items() if key not in {"reason", "history_confirmation", "parents_repayment", "immediate_account_id", "paid_without_account"}})
        return self

    def to_draft(self):
        return EntryDraft(**{key: value for key, value in self.model_dump().items() if key not in {"reason", "history_confirmation", "parents_repayment", "immediate_account_id", "paid_without_account"}})


Reason = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=1000)]


class EditInput(EntryInput):
    reason: Reason

    @model_validator(mode="after")
    def no_payment_on_edit(self):
        if self.paid_without_account:
            raise ValueError("A indicação de pagamento sem conta pertence somente ao novo cadastro.")
        return self


class CancelInput(InputModel):
    request_key: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=128)]
    reason: Reason



def civil_month(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}", value) is None:
        raise ValueError("Informe um ciclo no formato AAAA-MM.")
    try:
        return date.fromisoformat(value + "-01")
    except ValueError:
        raise ValueError("Informe um mês e um ano válidos.") from None


MonthInput = Annotated[date, BeforeValidator(civil_month)]
