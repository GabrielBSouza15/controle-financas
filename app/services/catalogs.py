"""Cadastros básicos explícitos; inicialização não cria movimentações."""

from datetime import date
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session
from app.domain import MERCADO_PAGO, NUBANK
from app.persistence.database import transaction
from app.persistence.entries import PersistenceValidationError
from app.persistence.models import Account, Card, CardRuleVersion, Category, Responsible, HistoryBatch, HistoryRecord


class NotFoundError(PersistenceValidationError):
    pass


class ConflictError(PersistenceValidationError):
    pass


def catalogs(engine):
    with Session(engine) as session:
        responsibles = [{"id": r.id, "code": r.code, "name": r.name} for r in session.scalars(select(Responsible).order_by(Responsible.id))]
        accounts = [{"id": a.id, "name": a.name, "owner_id": a.owner_id} for a in session.scalars(select(Account).order_by(Account.name))]
        categories = [{"id": c.id, "name": c.name, "parent_id": c.parent_id} for c in session.scalars(select(Category).order_by(Category.name))]
        # Sugestões são apenas leitura: não reclassificam nem criam cadastros do legado.
        # Os ensaios de atualização também preparam cadastros em esquemas antigos.
        schema = inspect(session.connection())
        historical_names = []
        if schema.has_table("history_records") and schema.has_table("history_batches"):
            historical_names = session.scalars(
                select(HistoryRecord.category_original).join(HistoryBatch)
                .where(HistoryBatch.posted.is_(True), HistoryRecord.role == "obligation",
                       HistoryRecord.category_original.is_not(None)).distinct()
            )
        suggestions = sorted({c["name"] for c in categories} |
                             {name.strip() for name in historical_names if name.strip()},
                             key=lambda name: (name.casefold(), name))
        rules = list(session.scalars(select(CardRuleVersion).order_by(CardRuleVersion.effective_from)))
        cards = [{"id": c.id, "name": c.name, "active": c.active, "holder_id": c.holder_id,
                  "rules": [{"effective_from": r.effective_from.isoformat(), "closing_day": r.closing_day,
                             "due_day": r.due_day, "include_closing_day": r.include_closing_day,
                             "due_month_offset": r.due_month_offset} for r in rules if r.card_id == c.id]}
                 for c in session.scalars(select(Card).order_by(Card.name))]
    return {"responsibles": responsibles, "accounts": accounts, "categories": categories, "category_suggestions": suggestions, "cards": cards,
            "ready": {r["code"] for r in responsibles} == {"self", "parents"}}


def initialize_catalogs(engine, effective_from: date):
    with transaction(engine) as session:
        for code, name in [("self", "Eu"), ("parents", "Pai/Mãe")]:
            if session.scalar(select(Responsible).where(Responsible.code == code)) is None:
                session.add(Responsible(code=code, name=name))
        session.flush()
        owner = session.scalar(select(Responsible).where(Responsible.code == "self"))
        for preset in [NUBANK, MERCADO_PAGO]:
            card = session.scalar(select(Card).where(Card.name == preset.name))
            if card is None:
                card = Card(name=preset.name, holder_id=owner.id)
                session.add(card)
                session.flush()
                session.add(CardRuleVersion(card_id=card.id, effective_from=effective_from,
                    closing_day=preset.closing_day, due_day=preset.due_day,
                    include_closing_day=preset.include_closing_day, due_month_offset=preset.due_month_offset))
            else:
                versions = list(session.scalars(select(CardRuleVersion).where(CardRuleVersion.card_id == card.id)))
                if card.holder_id != owner.id or len(versions) != 1 or any(
                    (r.effective_from, r.closing_day, r.due_day, r.include_closing_day, r.due_month_offset) !=
                    (effective_from, preset.closing_day, preset.due_day, preset.include_closing_day, preset.due_month_offset)
                    for r in versions
                ):
                    raise ConflictError("Os cartões já possuem outra configuração. A preparação não altera regras existentes.")
    return catalogs(engine)


def add_account(engine, name: str):
    with transaction(engine) as session:
        owner = session.scalar(select(Responsible).where(Responsible.code == "self"))
        if owner is None:
            raise ConflictError("Prepare os cadastros iniciais antes de cadastrar sua conta.")
        account = Account(name=name, owner_id=owner.id)
        session.add(account)
        session.flush()
        return {"id": account.id, "name": account.name, "owner_id": owner.id}


def add_category(engine, name: str, parent_id: int | None):
    with transaction(engine) as session:
        if parent_id is not None and session.get(Category, parent_id) is None:
            raise NotFoundError("A categoria superior não foi encontrada.")
        category = Category(name=name, parent_id=parent_id)
        session.add(category)
        session.flush()
        return {"id": category.id, "name": category.name, "parent_id": category.parent_id}
