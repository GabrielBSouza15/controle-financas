"""Evidência explícita de pagamento da fatura sem movimentação por conta."""
from alembic import op
import sqlalchemy as sa
from importlib import import_module

revision = "0017_bill_payment_marks"
down_revision = "0016_marked_entry_changes"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("bill_payment_marks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("operation_key", sa.String(128), sa.ForeignKey("personal_operations.request_key", ondelete="RESTRICT"), nullable=False),
        sa.Column("obligation_id", sa.Integer(), sa.ForeignKey("obligations.id", ondelete="RESTRICT"), unique=True),
        sa.Column("record_id", sa.Integer(), sa.ForeignKey("history_records.id", ondelete="RESTRICT"), unique=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.CheckConstraint("(obligation_id IS NOT NULL) != (record_id IS NOT NULL)", name="one_origin"),
        sa.CheckConstraint("typeof(amount_cents)='integer' AND amount_cents>0 AND amount_cents<=9223372036854775807", name="amount"),
        sa.CheckConstraint("length(occurred_on)=10 AND date(occurred_on) IS NOT NULL AND date(occurred_on)=occurred_on", name="civil_date"))
    # Preserve the existing view contract, adding only this separate payment evidence.
    original = import_module("migrations.versions.0012_history_operations").VIEWS["history_cycle_positions"]
    updated = original.replace(" AS new_paid_cents", " + COALESCE((SELECT SUM(m.amount_cents) FROM bill_payment_marks m JOIN history_current_records p ON p.id=m.record_id WHERE p.batch_id=r.batch_id AND p.card_code=r.card_code AND p.current_cycle=r.current_cycle),0) AS new_paid_cents")
    assert updated != original
    op.execute("DROP VIEW history_cycle_positions")
    op.execute(updated)
    credit = import_module("migrations.versions.0008_invoice_credits")
    bank_view = credit.VIEW.replace(" AS bank_paid", " + COALESCE((SELECT SUM(m.amount_cents) FROM bill_payment_marks m JOIN obligations o ON o.id=m.obligation_id WHERE o.invoice_id=i.id),0) AS bank_paid")
    assert bank_view != credit.VIEW
    op.execute("DROP VIEW invoice_issuer_balances")
    op.execute(bank_view)
    paid = f"COALESCE((SELECT SUM(a.amount_cents-({credit.OFFSET})) FROM settlement_allocations a JOIN allocation_batches b ON b.id=a.batch_id JOIN financial_events f ON f.id=b.event_id WHERE a.obligation_id=o.id AND b.posted=1 AND f.kind!='repayment' AND {credit.ACTIVE_EVENT}),0)"
    capacity = f"{credit.NET_AMOUNT}-({paid})-({credit.CREDIT_USED})"
    guards = {
        "capacity": ("INSERT", "bill_payment_marks", f"(NEW.obligation_id IS NOT NULL AND NEW.amount_cents>(SELECT {capacity} FROM obligations o WHERE o.id=NEW.obligation_id)) OR (NEW.record_id IS NOT NULL AND NEW.amount_cents>(SELECT r.effective_cents-r.baseline_cents-COALESCE((SELECT SUM(l.remaining_cents) FROM history_current_links l WHERE l.record_id=r.id AND l.event_kind!='repayment'),0) FROM history_current_records r WHERE r.id=NEW.record_id)) OR NEW.amount_cents+COALESCE((SELECT SUM(m.amount_cents) FROM bill_payment_marks m WHERE m.operation_key=NEW.operation_key),0)>(SELECT json_extract(p.result_json,'$.paid_cents') FROM personal_operations p WHERE p.request_key=NEW.operation_key)"),
        "credit_allocation": ("INSERT", "invoice_credit_allocations", "EXISTS (SELECT 1 FROM bill_payment_marks WHERE obligation_id=NEW.obligation_id)"),
        "batch_seal": ("UPDATE", "allocation_batches", "NEW.posted=1 AND EXISTS (SELECT 1 FROM bill_payment_marks m JOIN settlement_allocations a ON a.obligation_id=m.obligation_id JOIN financial_events f ON f.id=NEW.event_id WHERE a.batch_id=NEW.id AND f.kind!='repayment')"),
        "history_seal": ("UPDATE", "history_events", "NEW.posted=1 AND NEW.kind!='repayment' AND EXISTS (SELECT 1 FROM bill_payment_marks m JOIN history_allocations a ON a.record_id=m.record_id WHERE a.event_id=NEW.id)"),
        "credit_seal": ("UPDATE", "invoice_credit_applications", "NEW.posted=1 AND EXISTS (SELECT 1 FROM bill_payment_marks m JOIN invoice_credit_allocations a ON a.obligation_id=m.obligation_id WHERE a.application_id=NEW.id)"),
        "validate": ("INSERT", "bill_payment_marks", """NOT EXISTS (SELECT 1 FROM personal_operations p WHERE p.request_key=NEW.operation_key AND p.kind='bill_payment' AND json_extract(p.payload_json,'$.paid_without_account')=1 AND json_extract(p.payload_json,'$.account_id') IS NULL AND json_extract(p.payload_json,'$.occurred_on')=NEW.occurred_on)
        OR NEW.occurred_on>date('now','-3 hours')
        OR (NEW.obligation_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM obligations o JOIN entries e ON e.id=o.entry_id JOIN invoices i ON i.id=o.invoice_id JOIN personal_operations p ON p.request_key=NEW.operation_key WHERE o.id=NEW.obligation_id AND e.kind='expense' AND e.occurred_on<=NEW.occurred_on AND i.card_id=json_extract(p.payload_json,'$.card_id') AND i.reference_month=json_extract(p.payload_json,'$.month') AND NOT EXISTS (SELECT 1 FROM entry_changes c WHERE c.source_entry_id=e.id)))
        OR (NEW.record_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM history_current_records r JOIN cards c ON c.name=CASE r.card_code WHEN 'nubank' THEN 'Nubank' WHEN 'mercado_pago' THEN 'Mercado Pago/Livre' END JOIN personal_operations p ON p.request_key=NEW.operation_key WHERE r.id=NEW.record_id AND r.role='obligation' AND r.kind='expense' AND r.baseline_cents=0 AND c.id=json_extract(p.payload_json,'$.card_id') AND r.current_cycle=json_extract(p.payload_json,'$.month')))"""),
        "no_update": ("UPDATE", "bill_payment_marks", "1"),
        "no_delete": ("DELETE", "bill_payment_marks", "1"),
        "entry_change": ("INSERT", "entry_changes", "EXISTS (SELECT 1 FROM bill_payment_marks m JOIN obligations o ON o.id=m.obligation_id WHERE o.entry_id=NEW.source_entry_id)"),
        "allocation": ("INSERT", "settlement_allocations", "EXISTS (SELECT 1 FROM bill_payment_marks m JOIN allocation_batches b ON b.id=NEW.batch_id JOIN financial_events e ON e.id=b.event_id WHERE m.obligation_id=NEW.obligation_id AND e.kind!='repayment')"),
        "adjustment": ("INSERT", "obligation_adjustments", "EXISTS (SELECT 1 FROM bill_payment_marks WHERE obligation_id=NEW.obligation_id)"),
        "move": ("INSERT", "obligation_moves", "EXISTS (SELECT 1 FROM bill_payment_marks WHERE obligation_id=NEW.obligation_id)"),
        "history_allocation": ("INSERT", "history_allocations", "EXISTS (SELECT 1 FROM bill_payment_marks m JOIN history_events e ON e.id=NEW.event_id WHERE m.record_id=NEW.record_id AND e.kind!='repayment')"),
        "history_operation": ("INSERT", "history_operations", "EXISTS (SELECT 1 FROM bill_payment_marks WHERE record_id=NEW.record_id) AND NEW.kind!='reconcile'"),
        "reversal": ("INSERT", "event_reversals", "EXISTS (SELECT 1 FROM bill_payment_marks m JOIN settlement_allocations a ON a.obligation_id=m.obligation_id JOIN allocation_batches b ON b.id=a.batch_id JOIN financial_events e ON e.id=b.event_id WHERE e.id=NEW.event_id AND e.kind!='repayment')"),
        "history_reversal": ("INSERT", "history_event_reversals", "EXISTS (SELECT 1 FROM bill_payment_marks m JOIN history_allocations a ON a.record_id=m.record_id JOIN history_events e ON e.id=a.event_id WHERE e.id=NEW.event_id AND e.kind!='repayment')"),
        "correction": ("INSERT", "settlement_corrections", "EXISTS (SELECT 1 FROM bill_payment_marks m JOIN settlement_allocations a ON a.obligation_id=m.obligation_id JOIN allocation_batches b ON b.id=a.batch_id WHERE b.event_id=NEW.event_id AND EXISTS (SELECT 1 FROM financial_events f WHERE f.id=b.event_id AND f.kind!='repayment'))"),
    }
    for name, (action, table, condition) in guards.items():
        op.execute(f"CREATE TRIGGER bill_mark_{name} BEFORE {action} ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT,'preserve bill payment without account evidence'); END")

def downgrade():
    if op.get_bind().exec_driver_sql("SELECT count(*) FROM bill_payment_marks").scalar():
        raise RuntimeError("Preservar pagamentos; restaurar para arquivo novo.")
    names = op.get_bind().exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'bill_mark_%'").scalars().all()
    for name in names: op.execute('DROP TRIGGER "'+name+'"')
    op.execute("DROP VIEW history_cycle_positions")
    op.execute(import_module("migrations.versions.0012_history_operations").VIEWS["history_cycle_positions"])
    op.execute("DROP VIEW invoice_issuer_balances")
    op.execute(import_module("migrations.versions.0008_invoice_credits").VIEW)
    op.drop_table("bill_payment_marks")
