"""Correção explícita da cobertura inicial, sem reescrever regras ou compromissos."""
from datetime import date
from sqlalchemy import select
from app.domain import CardRule
from app.persistence.database import transaction
from app.persistence.entries import PersistenceValidationError
from app.persistence.models import Card, CardRuleVersion
from .catalogs import ConflictError, NotFoundError

_FIELDS = ("closing_day", "due_day", "include_closing_day", "due_month_offset")


def extend_initial_rule(engine, *, card_id: int, effective_from: date,
                        expected_rule_id: int, expected_rule: CardRule):
    """Adicionar cobertura anterior somente com regra conhecida e origem conferida.

    Não é executado ao abrir a aplicação ou ao cadastrar uma compra. O chamador
    deve obter autorização do escopo, fazer backup e registrar a intervenção.
    """
    if (type(effective_from) is not date or type(card_id) is not int or card_id < 1
            or type(expected_rule_id) is not int or expected_rule_id < 1
            or not isinstance(expected_rule, CardRule)):
        raise PersistenceValidationError("Informe cartão, data civil e regra de origem válidos.")
    with transaction(engine) as session:
        card = session.get(Card, card_id)
        if card is None or not card.active:
            raise NotFoundError("Cartão inexistente ou inativo.")
        source = session.get(CardRuleVersion, expected_rule_id)
        signature = tuple(getattr(expected_rule, field) for field in _FIELDS)
        if (source is None or source.card_id != card.id or card.name != expected_rule.name
                or tuple(getattr(source, field) for field in _FIELDS) != signature):
            raise ConflictError("A regra do cartão mudou. Confira a configuração antes de estender o período.")
        if effective_from >= source.effective_from:
            raise PersistenceValidationError("A extensão deve começar antes da regra de origem.")
        first = session.scalar(select(CardRuleVersion).where(
            CardRuleVersion.card_id == card.id).order_by(CardRuleVersion.effective_from))
        if first.effective_from == effective_from and tuple(getattr(first, field) for field in _FIELDS) == signature:
            return {"id": first.id, "created": False, "effective_from": effective_from.isoformat()}
        if first.id != source.id:
            raise ConflictError("A cobertura inicial mudou. Confira as regras antes de continuar.")
        rule = CardRuleVersion(card_id=card.id, effective_from=effective_from,
                               **dict(zip(_FIELDS, signature)))
        session.add(rule)
        session.flush()
        return {"id": rule.id, "created": True, "effective_from": effective_from.isoformat()}
