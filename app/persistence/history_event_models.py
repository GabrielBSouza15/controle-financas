"""Fatos novos vinculados às obrigações históricas, sem reescrever evidência."""
from datetime import date
from sqlalchemy import Integer,String,ForeignKey,Boolean,CheckConstraint,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from .models import Base
from .types import CivilDate,ExactInteger

class HistoryEvent(Base):
    __tablename__="history_events"
    id:Mapped[int]=mapped_column(Integer,primary_key=True)
    request_key:Mapped[str]=mapped_column(String(128),unique=True)
    kind:Mapped[str]=mapped_column(String(24))
    occurred_on:Mapped[date]=mapped_column(CivilDate())
    amount_cents:Mapped[int]=mapped_column(ExactInteger())
    payer_code:Mapped[str|None]=mapped_column(String(16))
    account_id:Mapped[int|None]=mapped_column(ForeignKey("accounts.id",ondelete="RESTRICT"))
    reason:Mapped[str]=mapped_column(String(1000))
    recorded_at:Mapped[str]=mapped_column(String(32))
    payload_json:Mapped[str]=mapped_column(String())
    posted:Mapped[bool]=mapped_column(Boolean(create_constraint=True,name="posted"),server_default="0")
    __table_args__=(
        CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128 AND length(trim(reason)) BETWEEN 1 AND 1000",name="audit"),
        CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991",name="amount"),
        CheckConstraint("COALESCE((kind='income_receipt' AND payer_code IS NULL AND account_id IS NOT NULL) OR (kind='repayment' AND payer_code='parents' AND account_id IS NOT NULL) OR (kind IN ('invoice_payment','expense_payment') AND ((payer_code='self' AND account_id IS NOT NULL) OR (payer_code='parents' AND account_id IS NULL))),0)",name="nature"),
    )

class HistoryAllocation(Base):
    __tablename__="history_allocations"
    id:Mapped[int]=mapped_column(Integer,primary_key=True)
    event_id:Mapped[int]=mapped_column(ForeignKey("history_events.id",ondelete="RESTRICT"))
    record_id:Mapped[int]=mapped_column(ForeignKey("history_records.id",ondelete="RESTRICT"))
    amount_cents:Mapped[int]=mapped_column(ExactInteger())
    __table_args__=(UniqueConstraint("event_id","record_id"),
        CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991",name="amount"))

class HistoryEventReversal(Base):
    __tablename__="history_event_reversals"
    id:Mapped[int]=mapped_column(Integer,primary_key=True)
    request_key:Mapped[str]=mapped_column(String(128),unique=True)
    event_id:Mapped[int]=mapped_column(ForeignKey("history_events.id",ondelete="RESTRICT"),unique=True)
    reason:Mapped[str]=mapped_column(String(1000))
    recorded_at:Mapped[str]=mapped_column(String(32))
    payload_json:Mapped[str]=mapped_column(String())
    __table_args__=(CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128 AND length(trim(reason)) BETWEEN 1 AND 1000",name="audit"),)
