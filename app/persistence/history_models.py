"""Histórico candidato: evidências observadas, sem inventar contratos ou caixa."""
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base
from .types import CivilDate, ExactInteger
from datetime import date

class HistoryBatch(Base):
    __tablename__ = "history_batches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    policy: Mapped[str] = mapped_column(String(100))
    recorded_at: Mapped[str] = mapped_column(String(32))
    record_count: Mapped[int] = mapped_column(ExactInteger())
    obligation_cents: Mapped[int] = mapped_column(ExactInteger())
    pending_cents: Mapped[int] = mapped_column(ExactInteger())
    decisions_json: Mapped[str] = mapped_column(String())
    posted: Mapped[bool] = mapped_column(Boolean(create_constraint=True,name="posted"),server_default="0")
    __table_args__ = (
        CheckConstraint("length(source_sha256)=64 AND source_sha256 NOT GLOB '*[^0-9a-f]*' AND length(payload_sha256)=64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'",name="hashes"),
        CheckConstraint("typeof(record_count)='integer' AND record_count>0",name="count"),
        CheckConstraint("typeof(obligation_cents)='integer' AND obligation_cents BETWEEN 0 AND 9007199254740991 AND typeof(pending_cents)='integer' AND pending_cents BETWEEN 0 AND obligation_cents",name="totals"),
    )

class HistoryRecord(Base):
    __tablename__ = "history_records"
    id: Mapped[int] = mapped_column(Integer,primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("history_batches.id",ondelete="RESTRICT"))
    source_row: Mapped[int] = mapped_column(ExactInteger())
    role: Mapped[str] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(16))
    amount_cents: Mapped[int] = mapped_column(ExactInteger())
    reference_on: Mapped[date] = mapped_column(CivilDate())
    observed_number: Mapped[int] = mapped_column(ExactInteger())
    observed_count: Mapped[int] = mapped_column(ExactInteger())
    responsible_code: Mapped[str] = mapped_column(String(16))
    payment_method: Mapped[str] = mapped_column(String(16))
    card_code: Mapped[str | None] = mapped_column(String(16))
    simulated_cycle: Mapped[date | None] = mapped_column(CivilDate())
    category_original: Mapped[str | None] = mapped_column(String(100))
    paid_evidence: Mapped[bool] = mapped_column(Boolean(create_constraint=True,name="paid_evidence"))
    group_key: Mapped[str | None] = mapped_column(String(100))
    group_state: Mapped[str] = mapped_column(String(16))
    evidence_json: Mapped[str] = mapped_column(String())
    __table_args__ = (
        UniqueConstraint("batch_id","source_row"),
        CheckConstraint("typeof(source_row)='integer' AND source_row BETWEEN 1 AND 1048576",name="source_row"),
        CheckConstraint("role IN ('obligation','opening_balance','excluded')",name="role"),
        CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991",name="amount"),
        CheckConstraint("typeof(observed_number)='integer' AND typeof(observed_count)='integer' AND observed_number BETWEEN 1 AND observed_count",name="parts"),
        CheckConstraint("kind IN ('expense','income') AND responsible_code IN ('self','parents') AND payment_method IN ('credit','pix','cash')",name="classification"),
        CheckConstraint("(payment_method='credit' AND kind='expense' AND card_code IN ('nubank','mercado_pago') AND simulated_cycle IS NOT NULL) OR (payment_method!='credit' AND card_code IS NULL AND simulated_cycle IS NULL)",name="card"),
        CheckConstraint("group_state IN ('confirmed','candidate','unmapped') AND ((group_state='unmapped' AND group_key IS NULL) OR (group_state!='unmapped' AND group_key IS NOT NULL))",name="group"),
        CheckConstraint("role!='opening_balance' OR (payment_method!='credit' AND responsible_code='self' AND observed_number=1 AND observed_count=1)",name="opening"),
    )
