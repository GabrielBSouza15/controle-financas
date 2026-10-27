"""Escolha estática do mês, independente de repasse financeiro registrado."""
from sqlalchemy import inspect, select
from app.domain.dates import add_months
from app.persistence.models import ParentBudgetChoice, Entry, Obligation
from app.persistence.entries import PersistenceValidationError
from .catalogs import ConflictError
from .settlements import now_text

def choices(session):
    if "parent_budget_choices" not in session.info:
        if not inspect(session.connection()).has_table("parent_budget_choices"):
            session.info["parent_budget_choices"] = {}
        else:
            session.info["parent_budget_choices"] = {r.entry_id:r.repayment_status for r in session.scalars(select(ParentBudgetChoice))}
    return session.info["parent_budget_choices"]

def register(session, entry_id, status, *, created):
    current=choices(session).get(entry_id)
    if not created:
        if current != status:
            raise ConflictError("A chave já foi usada com outra indicação de repasse. Edite o lançamento para revisar o planejamento.")
        return
    if status is None:
        return
    if status not in {"received","pending"}:
        raise PersistenceValidationError("Informe se o repasse foi recebido ou ainda está pendente.")
    # A trigger confirma também a responsabilidade, a natureza e o meio.
    entry=session.get(Entry,entry_id)
    from app.persistence.models import Responsible
    person=session.get(Responsible,entry.responsible_id)
    if person.code!="parents" or entry.kind!="expense" or entry.payment_method=="credit":
        raise PersistenceValidationError("A indicação de repasse pertence somente a despesas de Pai/Mãe via PIX/Débito.")
    if status=="pending":
        for part in session.scalars(select(Obligation).where(Obligation.entry_id==entry_id)):
            add_months(part.scheduled_date.replace(day=1),1)
    session.add(ParentBudgetChoice(entry_id=entry_id,repayment_status=status,recorded_at=now_text()))
    session.flush()
    session.info.pop("parent_budget_choices",None)

def budget_month(scheduled, status):
    month=scheduled.replace(day=1)
    return add_months(month,1) if status=="pending" else month
