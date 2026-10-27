"""Justificativa imutável de lançamento distinto com coincidência histórica."""
from sqlalchemy import Integer, String, ForeignKey, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base

class HistoryEntryReview(Base):
    __tablename__ = "history_entry_reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id", ondelete="RESTRICT"), unique=True)
    reason: Mapped[str] = mapped_column(String(1000))
    matches_json: Mapped[str] = mapped_column(String())
    recorded_at: Mapped[str] = mapped_column(String(32))
    __table_args__ = (
        CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000", name="reason"),
        CheckConstraint("json_valid(matches_json) AND json_type(matches_json)='array' AND json_array_length(matches_json)>0", name="matches"),
    )
