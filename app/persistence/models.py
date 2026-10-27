"""Modelo relacional inicial. O esquema é criado por migrações, não create_all."""

from datetime import date
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, MetaData, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, declared_attr
from .types import CivilDate, ExactInteger


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention={
        "ix": "ix_%(column_0_label)s", "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s", "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


class Responsible(Base):
    __tablename__ = "responsibles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    __table_args__ = (
        CheckConstraint("code IN ('self', 'parents')", name="code"),
        CheckConstraint("length(trim(name)) > 0", name="name"),
    )


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("responsibles.id", ondelete="RESTRICT"))
    __table_args__ = (CheckConstraint("length(trim(name)) > 0", name="name"),)


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"))
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="name"),
        CheckConstraint("parent_id IS NULL OR parent_id != id", name="parent"),
    )


class Card(Base):
    __tablename__ = "cards"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    holder_id: Mapped[int] = mapped_column(ForeignKey("responsibles.id", ondelete="RESTRICT"))
    active: Mapped[bool] = mapped_column(Boolean(create_constraint=True, name="active"), server_default="1")
    __table_args__ = (CheckConstraint("length(trim(name)) > 0", name="name"),)


class CardRuleVersion(Base):
    __tablename__ = "card_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="RESTRICT"), index=True)
    effective_from: Mapped[date] = mapped_column(CivilDate())
    closing_day: Mapped[int] = mapped_column(ExactInteger())
    due_day: Mapped[int] = mapped_column(ExactInteger())
    include_closing_day: Mapped[bool] = mapped_column(Boolean(create_constraint=True, name="include_closing_day"))
    due_month_offset: Mapped[int] = mapped_column(ExactInteger(), server_default="0")
    __table_args__ = (
        UniqueConstraint("card_id", "effective_from"),
        CheckConstraint("closing_day BETWEEN 1 AND 31 AND typeof(closing_day) = 'integer'", name="closing_day"),
        CheckConstraint("due_day BETWEEN 1 AND 31 AND typeof(due_day) = 'integer'", name="due_day"),
        CheckConstraint("due_month_offset IN (0, 1) AND typeof(due_month_offset) = 'integer'", name="offset"),
        CheckConstraint("due_month_offset = 1 OR due_day >= closing_day", name="due_order"),
    )


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="RESTRICT"), index=True)
    reference_month: Mapped[date] = mapped_column(CivilDate())
    closing_date: Mapped[date] = mapped_column(CivilDate())
    due_date: Mapped[date] = mapped_column(CivilDate())
    status: Mapped[str] = mapped_column(String(16), server_default="open")
    __table_args__ = (
        UniqueConstraint("card_id", "reference_month"),
        CheckConstraint("status IN ('open', 'closed')", name="status"),
        CheckConstraint("due_date >= closing_date", name="due_order"),
        CheckConstraint("strftime('%d', reference_month) = '01' AND strftime('%Y-%m', reference_month) = strftime('%Y-%m', closing_date)", name="reference_month"),
    )


class Entry(Base):
    __tablename__ = "entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str | None] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(16))
    total_cents: Mapped[int] = mapped_column(ExactInteger())
    installment_count: Mapped[int] = mapped_column(ExactInteger())
    occurred_on: Mapped[date] = mapped_column(CivilDate())
    payment_method: Mapped[str] = mapped_column(String(16))
    responsible_id: Mapped[int] = mapped_column(ForeignKey("responsibles.id", ondelete="RESTRICT"), index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    card_id: Mapped[int | None] = mapped_column(ForeignKey("cards.id", ondelete="RESTRICT"))
    card_rule_id: Mapped[int | None] = mapped_column(ForeignKey("card_rules.id", ondelete="RESTRICT"))
    obligations: Mapped[list["Obligation"]] = relationship(back_populates="entry", order_by="Obligation.number", passive_deletes="all")
    __table_args__ = (
        CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128", name="request_key"),
        CheckConstraint("description IS NULL OR length(trim(description)) > 0", name="description"),
        CheckConstraint("kind IN ('expense', 'income')", name="kind"),
        CheckConstraint("payment_method IN ('credit', 'pix', 'cash')", name="payment_method"),
        CheckConstraint("total_cents > 0 AND typeof(total_cents) = 'integer'", name="total"),
        CheckConstraint("installment_count BETWEEN 1 AND total_cents AND typeof(installment_count) = 'integer'", name="count"),
        CheckConstraint("(payment_method = 'credit' AND kind = 'expense' AND card_id IS NOT NULL AND card_rule_id IS NOT NULL AND account_id IS NULL) OR (payment_method != 'credit' AND card_id IS NULL AND card_rule_id IS NULL)", name="payment_links"),
    )


class Obligation(Base):
    __tablename__ = "obligations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id", ondelete="RESTRICT"), index=True)
    number: Mapped[int] = mapped_column(ExactInteger())
    amount_cents: Mapped[int] = mapped_column(ExactInteger())
    scheduled_date: Mapped[date] = mapped_column(CivilDate())
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"), index=True)
    entry: Mapped[Entry] = relationship(back_populates="obligations")
    __table_args__ = (
        UniqueConstraint("entry_id", "number"),
        CheckConstraint("number > 0 AND typeof(number) = 'integer'", name="number"),
        CheckConstraint("amount_cents > 0 AND typeof(amount_cents) = 'integer'", name="amount"),
    )

class EntryChange(Base):
    """Transição imutável; os lançamentos e cronogramas anteriores permanecem."""
    __tablename__ = "entry_changes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    source_entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id", ondelete="RESTRICT"), unique=True)
    replacement_entry_id: Mapped[int | None] = mapped_column(ForeignKey("entries.id", ondelete="RESTRICT"), unique=True)
    action: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(1000))
    recorded_at: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[str] = mapped_column(String())
    __table_args__ = (
        CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128", name="request_key"),
        CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000", name="reason"),
        CheckConstraint("(action='replace' AND replacement_entry_id IS NOT NULL AND replacement_entry_id > source_entry_id) OR (action='cancel' AND replacement_entry_id IS NULL)", name="transition"),
    )


class FinancialEvent(Base):
    """Fato informado: caixa próprio quando há conta; nunca nova compra."""
    __tablename__ = "financial_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[str] = mapped_column(String(24))
    occurred_on: Mapped[date] = mapped_column(CivilDate())
    amount_cents: Mapped[int] = mapped_column(ExactInteger())
    payer_id: Mapped[int | None] = mapped_column(ForeignKey("responsibles.id", ondelete="RESTRICT"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"))
    reason: Mapped[str] = mapped_column(String(1000))
    recorded_at: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[str] = mapped_column(String())
    __table_args__ = (
        CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128", name="request_key"),
        CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000", name="reason"),
        CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991", name="amount"),
        CheckConstraint("kind IN ('invoice_payment','repayment','expense_payment','income_receipt')", name="kind"),
        CheckConstraint("(kind='income_receipt' AND payer_id IS NULL) OR (kind!='income_receipt' AND payer_id IS NOT NULL)", name="payer"),
        CheckConstraint("(kind='invoice_payment' AND invoice_id IS NOT NULL) OR (kind!='invoice_payment' AND invoice_id IS NULL)", name="invoice"),
    )


class AllocationBatch(Base):
    __tablename__ = "allocation_batches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("financial_events.id", ondelete="RESTRICT"), index=True)
    amount_cents: Mapped[int] = mapped_column(ExactInteger())
    posted: Mapped[bool] = mapped_column(Boolean(create_constraint=True, name="posted"), server_default="0")
    reason: Mapped[str] = mapped_column(String(1000))
    recorded_at: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[str] = mapped_column(String())
    __table_args__ = (
        CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128", name="request_key"),
        CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000", name="reason"),
        CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 0 AND 9007199254740991", name="amount"),
    )


class SettlementAllocation(Base):
    __tablename__ = "settlement_allocations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("allocation_batches.id", ondelete="RESTRICT"), index=True)
    obligation_id: Mapped[int] = mapped_column(ForeignKey("obligations.id", ondelete="RESTRICT"), index=True)
    amount_cents: Mapped[int] = mapped_column(ExactInteger())
    __table_args__ = (
        UniqueConstraint("batch_id", "obligation_id"),
        CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991", name="amount"),
    )


class EventReversal(Base):
    __tablename__ = "event_reversals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("financial_events.id", ondelete="RESTRICT"), unique=True)
    reason: Mapped[str] = mapped_column(String(1000))
    recorded_at: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[str] = mapped_column(String())
    __table_args__ = (
        CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128", name="request_key"),
        CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000", name="reason"),
    )


class InvoiceClosure(Base):
    __tablename__ = "invoice_closures"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"), unique=True)
    total_cents: Mapped[int] = mapped_column(ExactInteger())
    reason: Mapped[str] = mapped_column(String(1000))
    recorded_at: Mapped[str] = mapped_column(String(32))
    snapshot_json: Mapped[str] = mapped_column(String())
    payload_json: Mapped[str] = mapped_column(String())
    __table_args__ = (
        CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128", name="request_key"),
        CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000", name="reason"),
        CheckConstraint("typeof(total_cents)='integer' AND total_cents BETWEEN 0 AND 9007199254740991", name="total"),
    )


class Audit:
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    reason: Mapped[str] = mapped_column(String(1000))
    recorded_at: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[str] = mapped_column(String())
    @declared_attr.directive
    def __table_args__(cls):
        return (CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128", name="request_key"),
                CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000", name="reason"))

class ObligationAdjustment(Audit, Base):
    __tablename__ = "obligation_adjustments"
    obligation_id: Mapped[int] = mapped_column(ForeignKey("obligations.id", ondelete="RESTRICT"))
    delta_cents: Mapped[int] = mapped_column(ExactInteger())
    occurred_on: Mapped[date] = mapped_column(CivilDate())
    __table_args__ = (
        CheckConstraint("typeof(delta_cents)='integer' AND delta_cents != 0 AND abs(delta_cents)<=9007199254740991", name="delta"),
        CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128", name="request_key"),
        CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000", name="reason"),
    )

class SettlementCorrection(Audit, Base):
    __tablename__ = "settlement_corrections"
    event_id: Mapped[int] = mapped_column(ForeignKey("financial_events.id", ondelete="RESTRICT"))
    action: Mapped[str] = mapped_column(String(16))
    occurred_on: Mapped[date] = mapped_column(CivilDate())
    amount_cents: Mapped[int] = mapped_column(ExactInteger())
    unallocated_cents: Mapped[int] = mapped_column(ExactInteger(), server_default="0")
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    posted: Mapped[bool] = mapped_column(Boolean(create_constraint=True, name="posted"), server_default="0")
    __table_args__ = (
        CheckConstraint("action IN ('release','refund')", name="action"),
        CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991", name="amount"),
        CheckConstraint("typeof(unallocated_cents)='integer' AND unallocated_cents BETWEEN 0 AND amount_cents", name="unallocated"),
        CheckConstraint("action='refund' OR (account_id IS NULL AND unallocated_cents=0)", name="release"),
        CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128", name="request_key"),
        CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000", name="reason"),
    )

class AllocationOffset(Base):
    __tablename__ = "allocation_offsets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    correction_id: Mapped[int] = mapped_column(ForeignKey("settlement_corrections.id", ondelete="RESTRICT"))
    allocation_id: Mapped[int] = mapped_column(ForeignKey("settlement_allocations.id", ondelete="RESTRICT"))
    amount_cents: Mapped[int] = mapped_column(ExactInteger())
    __table_args__ = (UniqueConstraint("correction_id","allocation_id"),
        CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991", name="amount"))

class CashTransfer(Audit, Base):
    __tablename__ = "cash_transfers"
    source_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    target_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    occurred_on: Mapped[date] = mapped_column(CivilDate())
    amount_cents: Mapped[int] = mapped_column(ExactInteger())
    __table_args__ = (
        CheckConstraint("source_account_id!=target_account_id", name="accounts"),
        CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991", name="amount"),
        CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128", name="request_key"),
        CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000", name="reason"),
    )

class TransferReversal(Audit, Base):
    __tablename__ = "transfer_reversals"
    transfer_id: Mapped[int] = mapped_column(ForeignKey("cash_transfers.id", ondelete="RESTRICT"), unique=True)

class InvoiceReconciliation(Audit, Base):
    __tablename__ = "invoice_reconciliations"
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"))
    expected_total_cents: Mapped[int] = mapped_column(ExactInteger())
    calculated_total_cents: Mapped[int] = mapped_column(ExactInteger())
    snapshot_json: Mapped[str] = mapped_column(String())
    __table_args__ = (
        CheckConstraint("typeof(expected_total_cents)='integer' AND abs(expected_total_cents)<=9007199254740991", name="expected"),
        CheckConstraint("typeof(calculated_total_cents)='integer' AND abs(calculated_total_cents)<=9007199254740991", name="calculated"),
        CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128", name="request_key"),
        CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000", name="reason"),
    )

class RecurrenceSeries(Audit, Base):
    __tablename__ = "recurrence_series"
    description: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(16))
    amount_cents: Mapped[int] = mapped_column(ExactInteger())
    start_on: Mapped[date] = mapped_column(CivilDate())
    end_on: Mapped[date | None] = mapped_column(CivilDate())
    payment_method: Mapped[str] = mapped_column(String(16))
    responsible_id: Mapped[int] = mapped_column(ForeignKey("responsibles.id", ondelete="RESTRICT"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    card_id: Mapped[int | None] = mapped_column(ForeignKey("cards.id", ondelete="RESTRICT"))
    __table_args__ = (
        CheckConstraint("length(trim(description)) BETWEEN 1 AND 500", name="description"),
        CheckConstraint("kind IN ('expense','income') AND payment_method IN ('credit','pix','cash')", name="kind_method"),
        CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991", name="amount"),
        CheckConstraint("end_on IS NULL OR end_on>=start_on", name="end"),
        CheckConstraint("(payment_method='credit' AND card_id IS NOT NULL AND account_id IS NULL AND kind='expense') OR (payment_method!='credit' AND card_id IS NULL)", name="links"),
        CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128", name="request_key"),
        CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000", name="reason"),
    )

class RecurrenceChange(Audit, Base):
    __tablename__ = "recurrence_changes"
    series_id: Mapped[int] = mapped_column(ForeignKey("recurrence_series.id", ondelete="RESTRICT"))
    effective_month: Mapped[date] = mapped_column(CivilDate())
    amount_cents: Mapped[int | None] = mapped_column(ExactInteger())
    suspended: Mapped[bool | None] = mapped_column(Boolean(create_constraint=True, name="suspended"))
    __table_args__ = (
        CheckConstraint("strftime('%d',effective_month)='01'", name="month"),
        CheckConstraint("amount_cents IS NOT NULL OR suspended IS NOT NULL", name="change"),
        CheckConstraint("amount_cents IS NULL OR (typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991)", name="amount"),
        CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128", name="request_key"),
        CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000", name="reason"),
    )

class RecurrenceOccurrence(Audit, Base):
    __tablename__ = "recurrence_occurrences"
    series_id: Mapped[int] = mapped_column(ForeignKey("recurrence_series.id", ondelete="RESTRICT"))
    reference_month: Mapped[date] = mapped_column(CivilDate())
    scheduled_on: Mapped[date] = mapped_column(CivilDate())
    expected_cents: Mapped[int] = mapped_column(ExactInteger())
    actual_cents: Mapped[int | None] = mapped_column(ExactInteger())
    status: Mapped[str] = mapped_column(String(16))
    entry_id: Mapped[int | None] = mapped_column(ForeignKey("entries.id", ondelete="RESTRICT"), unique=True)
    __table_args__ = (
        UniqueConstraint("series_id","reference_month"),
        CheckConstraint("strftime('%d',reference_month)='01' AND strftime('%Y-%m',reference_month)=strftime('%Y-%m',scheduled_on)", name="month"),
        CheckConstraint("(status='confirmed' AND entry_id IS NOT NULL AND actual_cents IS NOT NULL) OR (status='skipped' AND entry_id IS NULL AND actual_cents IS NULL)", name="status"),
        CheckConstraint("typeof(expected_cents)='integer' AND expected_cents BETWEEN 1 AND 9007199254740991", name="expected"),
        CheckConstraint("actual_cents IS NULL OR (typeof(actual_cents)='integer' AND actual_cents BETWEEN 1 AND 9007199254740991)", name="actual"),
        CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128", name="request_key"),
        CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000", name="reason"),
    )

class ObligationMove(Audit, Base):
    __tablename__ = "obligation_moves"
    obligation_id: Mapped[int] = mapped_column(ForeignKey("obligations.id", ondelete="RESTRICT"))
    source_invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"))
    target_invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"))
    source_scheduled_on: Mapped[date] = mapped_column(CivilDate())
    target_scheduled_on: Mapped[date] = mapped_column(CivilDate())

class LegacyImportBatch(Base):
    __tablename__ = "legacy_import_batches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_sha256: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(100))
    original_name: Mapped[str] = mapped_column(String(500))
    recorded_at: Mapped[str] = mapped_column(String(32))
    extraction_sha256: Mapped[str] = mapped_column(String(64))
    control_json: Mapped[str] = mapped_column(String())
    context_json: Mapped[str] = mapped_column(String())
    __table_args__ = (UniqueConstraint("file_sha256","policy_version"),
        CheckConstraint("length(file_sha256)=64 AND file_sha256 NOT GLOB '*[^0-9a-f]*'", name="hash"),
        CheckConstraint("length(extraction_sha256)=64 AND extraction_sha256 NOT GLOB '*[^0-9a-f]*'", name="extraction_hash"))

class LegacySourceRow(Base):
    __tablename__ = "legacy_source_rows"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("legacy_import_batches.id", ondelete="RESTRICT"))
    sheet_name: Mapped[str] = mapped_column(String(100))
    row_number: Mapped[int] = mapped_column(ExactInteger())
    status: Mapped[str] = mapped_column(String(16))
    source_amount_decimal: Mapped[str | None] = mapped_column(String(128))
    raw_json: Mapped[str] = mapped_column(String())
    issues_json: Mapped[str] = mapped_column(String())
    proposal_json: Mapped[str] = mapped_column(String())
    __table_args__ = (UniqueConstraint("batch_id","sheet_name","row_number"),
        CheckConstraint("typeof(row_number)='integer' AND row_number BETWEEN 1 AND 1048576", name="row"),
        CheckConstraint("status IN ('accepted','pending','excluded')", name="status"))

class LegacyObligation(Base):
    __tablename__ = "legacy_obligations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_row_id: Mapped[int] = mapped_column(ForeignKey("legacy_source_rows.id", ondelete="RESTRICT"), unique=True)
    reference_on: Mapped[date] = mapped_column(CivilDate())
    description: Mapped[str | None] = mapped_column(String(500))
    normalized_description: Mapped[str] = mapped_column(String(500))
    amount_cents: Mapped[int] = mapped_column(ExactInteger())
    observed_number: Mapped[int] = mapped_column(ExactInteger())
    observed_count: Mapped[int] = mapped_column(ExactInteger())
    kind: Mapped[str] = mapped_column(String(16))
    responsible_code: Mapped[str] = mapped_column(String(16))
    payment_method: Mapped[str] = mapped_column(String(16))
    card_code: Mapped[str | None] = mapped_column(String(16))
    bank_original: Mapped[str | None] = mapped_column(String(100))
    category_original: Mapped[str | None] = mapped_column(String(100))
    paid_evidence: Mapped[bool] = mapped_column(Boolean(create_constraint=True, name="paid_evidence"))
    simulated_cycle: Mapped[date | None] = mapped_column(CivilDate())
    __table_args__ = (
        CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991", name="amount"),
        CheckConstraint("typeof(observed_number)='integer' AND typeof(observed_count)='integer' AND observed_number BETWEEN 1 AND observed_count", name="installments"),
        CheckConstraint("kind IN ('expense','income') AND responsible_code IN ('self','parents') AND payment_method IN ('credit','pix','cash')", name="classification"),
        CheckConstraint("(payment_method='credit' AND card_code IN ('nubank','mercado_pago') AND kind='expense' AND simulated_cycle IS NOT NULL) OR (payment_method!='credit' AND card_code IS NULL AND simulated_cycle IS NULL)", name="card"),
    )

class InvoiceCreditApplication(Audit, Base):
    __tablename__ = "invoice_credit_applications"
    source_invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"))
    target_invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"))
    occurred_on: Mapped[date] = mapped_column(CivilDate())
    amount_cents: Mapped[int] = mapped_column(ExactInteger())
    posted: Mapped[bool] = mapped_column(Boolean(create_constraint=True, name="posted"), server_default="0")
    __table_args__ = (
        CheckConstraint("source_invoice_id!=target_invoice_id", name="invoices"),
        CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991", name="amount"),
        CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128", name="request_key"),
        CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000", name="reason"),
    )

class InvoiceCreditAllocation(Base):
    __tablename__ = "invoice_credit_allocations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("invoice_credit_applications.id", ondelete="RESTRICT"))
    obligation_id: Mapped[int] = mapped_column(ForeignKey("obligations.id", ondelete="RESTRICT"))
    amount_cents: Mapped[int] = mapped_column(ExactInteger())
    __table_args__ = (UniqueConstraint("application_id","obligation_id"),
        CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991", name="amount"))

class InvoiceCreditReversal(Audit, Base):
    __tablename__ = "invoice_credit_reversals"
    application_id: Mapped[int] = mapped_column(ForeignKey("invoice_credit_applications.id", ondelete="RESTRICT"), unique=True)

# Registrar as tabelas históricas no mesmo metadata.
from .history_models import HistoryBatch, HistoryRecord

from .history_event_models import HistoryEvent, HistoryAllocation, HistoryEventReversal
from .history_entry_models import HistoryEntryReview

from .history_operation_models import HistoryOperation

from .adoption_models import HistoryAdoption

from .parent_budget_models import ParentBudgetChoice

from .personal_models import PersonalOperation
from .payment_mark_models import EntryPaymentMark
