"""Gravação atômica de lançamentos e cronogramas calculados pelo domínio."""

from dataclasses import dataclass, fields
from datetime import date
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from .database import transaction
from .models import Card, CardRuleVersion, Entry, EntryChange, Invoice, Obligation
from .types import MAX_INTEGER
from .settlements import financial_key_exists
from app.domain import CardRule, FinancialValidationError, schedule_card_installments, schedule_monthly_installments


class PersistenceValidationError(ValueError):
    """Requisição válida sintaticamente, mas incompatível com o estado persistido."""


class IdempotencyConflict(PersistenceValidationError):
    """Chave de operação já utilizada com dados diferentes."""


@dataclass(frozen=True, slots=True)
class EntryDraft:
    request_key: str
    description: str
    occurred_on: date
    total_cents: int
    installment_count: int
    responsible_id: int
    payment_method: str
    kind: str = "expense"
    category_id: int | None = None
    account_id: int | None = None
    card_id: int | None = None

    def __post_init__(self):
        for name, limit in [("request_key", 128), ("description", 500)]:
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise PersistenceValidationError(f"{name}: texto obrigatório de até {limit} caracteres.")
        if type(self.occurred_on) is not date:
            raise PersistenceValidationError("Informe uma data civil sem horário.")
        for name in ["total_cents", "installment_count", "responsible_id", "category_id", "account_id", "card_id"]:
            value = getattr(self, name)
            if value is None and name in {"category_id", "account_id", "card_id"}:
                continue
            if type(value) is not int or not 1 <= value <= MAX_INTEGER:
                raise PersistenceValidationError(f"{name}: inteiro positivo de 64 bits obrigatório.")
        if self.installment_count > self.total_cents:
            raise PersistenceValidationError("Cada parcela precisa ter ao menos um centavo.")
        if self.kind not in ("expense", "income") or self.payment_method not in ("credit", "pix", "cash"):
            raise PersistenceValidationError("Tipo ou meio de pagamento inválido.")
        if self.payment_method == "credit":
            if self.card_id is None or self.account_id is not None or self.kind != "expense":
                raise PersistenceValidationError("Crédito exige cartão e despesa, sem saída de conta neste cadastro.")
        elif self.card_id is not None:
            raise PersistenceValidationError("Cartão só é aplicável ao meio crédito.")


def record_entry(engine: Engine, draft: EntryDraft) -> int:
    """Retorna o id; mesma chave/dados repete o resultado, chave/dados distintos falha.

    A função possui sua própria transação. Não cria pagamentos ou movimentos
    de caixa. Erros de integridade/bloqueio propagam sem gravação parcial.
    """
    if not isinstance(draft, EntryDraft):
        raise PersistenceValidationError("Informe um EntryDraft válido.")
    with transaction(engine) as session:
        if session.scalar(select(EntryChange.id).where(EntryChange.request_key == draft.request_key)):
            raise IdempotencyConflict("Esta chave pertence ao histórico de alterações.")
        return record_entry_in_session(session, draft)


def record_entry_in_session(session, draft: EntryDraft, history_confirmation=None) -> int:
    """Mesma gravação, componível na transação de uma revisão; não faz commit."""
    if not isinstance(draft, EntryDraft):
        raise PersistenceValidationError("Informe um EntryDraft válido.")
    if financial_key_exists(session, draft.request_key):
        raise IdempotencyConflict("Esta chave pertence ao histórico financeiro.")
    previous = session.scalar(select(Entry).where(Entry.request_key == draft.request_key))
    if previous is not None:
        if any(getattr(previous, item.name) != getattr(draft, item.name) for item in fields(draft)):
            raise IdempotencyConflict("A chave já foi usada com outros dados; não será gravado outro lançamento.")
        from .history_overlap import replay_review
        replay_review(session, previous.id, history_confirmation)
        return previous.id

    version = None
    if draft.payment_method == "credit":
        card = session.get(Card, draft.card_id)
        if card is None or not card.active:
            raise PersistenceValidationError("Cartão inexistente ou inativo.")
        version = session.scalar(select(CardRuleVersion).where(
            CardRuleVersion.card_id == card.id,
            CardRuleVersion.effective_from <= draft.occurred_on,
        ).order_by(CardRuleVersion.effective_from.desc()))
        if version is None:
            first = session.scalar(select(CardRuleVersion).where(
                CardRuleVersion.card_id == card.id).order_by(CardRuleVersion.effective_from))
            if first is not None:
                raise PersistenceValidationError(
                    f"{card.name} está configurado somente a partir de {first.effective_from:%d/%m/%Y}. "
                    "Para registrar uma compra anterior, confirme as regras do cartão para esse período."
                )
            raise PersistenceValidationError(
                f"{card.name} ainda não possui regras de fechamento e vencimento cadastradas."
            )
        rule = CardRule(card.name, version.closing_day, version.due_day, version.include_closing_day, version.due_month_offset)
        schedule = schedule_card_installments(draft.total_cents, draft.installment_count, draft.occurred_on, rule)
    else:
        schedule = schedule_monthly_installments(draft.total_cents, draft.installment_count, draft.occurred_on)

    entry = Entry(**{item.name: getattr(draft, item.name) for item in fields(draft)}, card_rule_id=version.id if version else None)
    session.add(entry)
    session.flush()
    for part in schedule:
        invoice_id = None
        if draft.payment_method == "credit":
            reference = part.cycle.closing_date.replace(day=1)
            invoice = session.scalar(select(Invoice).where(Invoice.card_id == draft.card_id, Invoice.reference_month == reference))
            if invoice is None:
                invoice = Invoice(card_id=draft.card_id, reference_month=reference,
                                  closing_date=part.cycle.closing_date, due_date=part.cycle.due_date, status="open")
                session.add(invoice)
                session.flush()
            if invoice.status != "open":
                raise PersistenceValidationError("Fatura consolidada: conciliação/ajuste precisa de operação específica.")
            if (invoice.closing_date, invoice.due_date) != (part.cycle.closing_date, part.cycle.due_date):
                raise PersistenceValidationError("Regra conflitante com as datas da fatura existente; concilie explicitamente.")
            invoice_id = invoice.id
            scheduled = part.cycle.due_date
        else:
            scheduled = part.scheduled_date
        session.add(Obligation(entry_id=entry.id, number=part.number, amount_cents=part.amount_cents,
                               scheduled_date=scheduled, invoice_id=invoice_id))
        session.flush()
    count, total = session.execute(select(func.count(Obligation.id), func.sum(Obligation.amount_cents)).where(Obligation.entry_id == entry.id)).one()
    if count != draft.installment_count or total != draft.total_cents:
        raise FinancialValidationError("Cronograma incompleto ou divergente; transação será revertida.")
    from .history_overlap import check_history_overlap
    check_history_overlap(session, entry, history_confirmation)
    return entry.id
