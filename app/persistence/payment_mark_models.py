"""Evidência de quitação integral sem conta bancária atribuída."""
from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base


class EntryPaymentMark(Base):
    __tablename__="entry_payment_marks"
    entry_id: Mapped[int]=mapped_column(ForeignKey("entries.id",ondelete="RESTRICT"),primary_key=True)
    recorded_at: Mapped[str]=mapped_column(String(32))
    __table_args__=(CheckConstraint("length(trim(recorded_at)) > 0",name="recorded"),)
