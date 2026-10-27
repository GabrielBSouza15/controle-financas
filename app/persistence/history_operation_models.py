"""Operações históricas aditivas; a evidência importada permanece imutável."""
from datetime import date
from sqlalchemy import Integer, String, ForeignKey, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base
from .types import CivilDate, ExactInteger

SHAPE = """(
(kind='refund_credit' AND record_id IS NULL AND event_id IS NOT NULL AND allocation_id IS NULL AND source_operation_id IS NULL AND amount_cents>0 AND target_month IS NULL AND card_code IS NULL) OR

(kind='adjustment' AND record_id IS NOT NULL AND event_id IS NULL AND allocation_id IS NULL AND source_operation_id IS NULL AND amount_cents!=0 AND account_id IS NULL AND target_month IS NULL AND card_code IS NULL) OR
(kind='allocate' AND record_id IS NOT NULL AND event_id IS NOT NULL AND allocation_id IS NULL AND source_operation_id IS NULL AND amount_cents>0 AND account_id IS NULL AND target_month IS NULL AND card_code IS NULL) OR
(kind IN ('release','refund') AND record_id IS NOT NULL AND event_id IS NOT NULL AND ((allocation_id IS NOT NULL AND source_operation_id IS NULL) OR (allocation_id IS NULL AND source_operation_id IS NOT NULL)) AND amount_cents>0 AND (kind='refund' OR account_id IS NULL) AND target_month IS NULL AND card_code IS NULL) OR
(kind='baseline_refund' AND record_id IS NOT NULL AND event_id IS NULL AND allocation_id IS NULL AND source_operation_id IS NULL AND amount_cents>0 AND account_id IS NOT NULL AND target_month IS NULL AND card_code IS NULL) OR
(kind='move' AND record_id IS NOT NULL AND event_id IS NULL AND allocation_id IS NULL AND source_operation_id IS NULL AND amount_cents=0 AND account_id IS NULL AND target_month IS NOT NULL AND card_code IS NULL) OR
(kind='reconcile' AND record_id IS NULL AND event_id IS NULL AND allocation_id IS NULL AND source_operation_id IS NULL AND amount_cents>=0 AND account_id IS NULL AND target_month IS NOT NULL AND card_code IN ('nubank','mercado_pago') AND snapshot_json IS NOT NULL)
)"""
class HistoryOperation(Base):
    __tablename__ = "history_operations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[str] = mapped_column(String(24))
    record_id: Mapped[int|None] = mapped_column(ForeignKey("history_records.id",ondelete="RESTRICT"))
    event_id: Mapped[int|None] = mapped_column(ForeignKey("history_events.id",ondelete="RESTRICT"))
    allocation_id: Mapped[int|None] = mapped_column(ForeignKey("history_allocations.id",ondelete="RESTRICT"))
    source_operation_id: Mapped[int|None] = mapped_column(ForeignKey("history_operations.id",ondelete="RESTRICT"))
    amount_cents: Mapped[int] = mapped_column(ExactInteger())
    occurred_on: Mapped[date] = mapped_column(CivilDate())
    account_id: Mapped[int|None] = mapped_column(ForeignKey("accounts.id",ondelete="RESTRICT"))
    target_month: Mapped[date|None] = mapped_column(CivilDate())
    card_code: Mapped[str|None] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(1000))
    recorded_at: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[str] = mapped_column(String())
    snapshot_json: Mapped[str|None] = mapped_column(String())
    __table_args__ = (
        CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128 AND length(trim(reason)) BETWEEN 1 AND 1000",name="audit"),
        CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN -9007199254740991 AND 9007199254740991",name="amount"),
        CheckConstraint(SHAPE,name="shape"),
    )
