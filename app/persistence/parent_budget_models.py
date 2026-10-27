"""Decisão orçamentária explícita; não é liquidação nem fato de caixa."""
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base

class ParentBudgetChoice(Base):
    __tablename__ = "parent_budget_choices"
    entry_id: Mapped[int] = mapped_column(Integer, ForeignKey("entries.id", ondelete="RESTRICT"), primary_key=True)
    repayment_status: Mapped[str] = mapped_column(String(16))
    recorded_at: Mapped[str] = mapped_column(String(32))
    __table_args__ = (
        CheckConstraint("repayment_status IN ('received','pending')", name="status"),
        CheckConstraint("length(trim(recorded_at)) > 0", name="recorded"),
    )
