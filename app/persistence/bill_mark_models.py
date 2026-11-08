"""Quitação de parcelas de fatura sem atribuir uma conta bancária."""
from sqlalchemy import Column, Integer, String, Date, ForeignKey, CheckConstraint, UniqueConstraint
from .models import Base

class BillPaymentMark(Base):
    __tablename__ = "bill_payment_marks"
    id = Column(Integer, primary_key=True)
    operation_key = Column(String(128), ForeignKey("personal_operations.request_key", ondelete="RESTRICT"), nullable=False)
    obligation_id = Column(Integer, ForeignKey("obligations.id", ondelete="RESTRICT"), unique=True)
    record_id = Column(Integer, ForeignKey("history_records.id", ondelete="RESTRICT"), unique=True)
    amount_cents = Column(Integer, nullable=False)
    occurred_on = Column(Date, nullable=False)
    __table_args__ = (
        CheckConstraint("(obligation_id IS NOT NULL) != (record_id IS NOT NULL)", name="one_origin"),
        CheckConstraint("typeof(amount_cents)='integer' AND amount_cents>0 AND amount_cents<=9223372036854775807", name="amount"),
        CheckConstraint("length(occurred_on)=10 AND date(occurred_on) IS NOT NULL AND date(occurred_on)=occurred_on", name="civil_date"),
    )
