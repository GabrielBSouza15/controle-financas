"""Proteções de integridade e histórico no SQLite.

Revision ID: 0002_integrity
"""
from alembic import op

revision = "0002_integrity"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

TRIGGERS = {
    "entry_rule_insert": """
CREATE TRIGGER entry_rule_insert BEFORE INSERT ON entries
WHEN NEW.payment_method = 'credit' AND NOT EXISTS (
 SELECT 1 FROM card_rules WHERE id=NEW.card_rule_id AND card_id=NEW.card_id AND effective_from <= NEW.occurred_on)
BEGIN SELECT RAISE(ABORT, 'card rule does not belong to card or is not effective'); END
""",
    "obligation_valid_insert": """
CREATE TRIGGER obligation_valid_insert BEFORE INSERT ON obligations
BEGIN
 SELECT CASE WHEN NEW.number > (SELECT installment_count FROM entries WHERE id=NEW.entry_id)
 THEN RAISE(ABORT, 'installment number exceeds count') END;
 SELECT CASE WHEN NEW.amount_cents + COALESCE((SELECT SUM(amount_cents) FROM obligations WHERE entry_id=NEW.entry_id AND id != COALESCE(NEW.id, -1)), 0)
 > (SELECT total_cents FROM entries WHERE id=NEW.entry_id)
 THEN RAISE(ABORT, 'installments exceed entry total') END;
 SELECT CASE WHEN (SELECT payment_method FROM entries WHERE id=NEW.entry_id)='credit' AND
 NOT EXISTS (SELECT 1 FROM invoices i JOIN entries e ON e.id=NEW.entry_id WHERE i.id=NEW.invoice_id AND i.card_id=e.card_id AND i.due_date=NEW.scheduled_date)
 THEN RAISE(ABORT, 'invoice does not match entry card or due date') END;
 SELECT CASE WHEN (SELECT payment_method FROM entries WHERE id=NEW.entry_id)!='credit' AND NEW.invoice_id IS NOT NULL
 THEN RAISE(ABORT, 'non-credit installment cannot have invoice') END;
 SELECT CASE WHEN EXISTS (SELECT 1 FROM invoices WHERE id=NEW.invoice_id AND status='closed')
 THEN RAISE(ABORT, 'closed invoice cannot receive changes') END;
END
""",
    "entry_rule_update": """
CREATE TRIGGER entry_rule_update BEFORE UPDATE ON entries
WHEN NEW.payment_method = 'credit' AND NOT EXISTS (
 SELECT 1 FROM card_rules WHERE id=NEW.card_rule_id AND card_id=NEW.card_id AND effective_from <= NEW.occurred_on)
BEGIN SELECT RAISE(ABORT, 'card rule does not belong to card or is not effective'); END
""",
    "obligation_valid_update": """
CREATE TRIGGER obligation_valid_update BEFORE UPDATE ON obligations
BEGIN
 SELECT CASE WHEN NEW.number > (SELECT installment_count FROM entries WHERE id=NEW.entry_id)
 THEN RAISE(ABORT, 'installment number exceeds count') END;
 SELECT CASE WHEN NEW.amount_cents + COALESCE((SELECT SUM(amount_cents) FROM obligations WHERE entry_id=NEW.entry_id AND id != COALESCE(NEW.id, -1)), 0)
 > (SELECT total_cents FROM entries WHERE id=NEW.entry_id)
 THEN RAISE(ABORT, 'installments exceed entry total') END;
 SELECT CASE WHEN (SELECT payment_method FROM entries WHERE id=NEW.entry_id)='credit' AND
 NOT EXISTS (SELECT 1 FROM invoices i JOIN entries e ON e.id=NEW.entry_id WHERE i.id=NEW.invoice_id AND i.card_id=e.card_id AND i.due_date=NEW.scheduled_date)
 THEN RAISE(ABORT, 'invoice does not match entry card or due date') END;
 SELECT CASE WHEN (SELECT payment_method FROM entries WHERE id=NEW.entry_id)!='credit' AND NEW.invoice_id IS NOT NULL
 THEN RAISE(ABORT, 'non-credit installment cannot have invoice') END;
 SELECT CASE WHEN EXISTS (SELECT 1 FROM invoices WHERE id=NEW.invoice_id AND status='closed')
 THEN RAISE(ABORT, 'closed invoice cannot receive changes') END;
END
""",
    "closed_obligation_update": """
CREATE TRIGGER closed_obligation_update BEFORE UPDATE ON obligations
WHEN EXISTS (SELECT 1 FROM invoices WHERE id=OLD.invoice_id AND status='closed')
BEGIN SELECT RAISE(ABORT, 'closed invoice installment is immutable'); END
""",
    "closed_entry_update": """
CREATE TRIGGER closed_entry_update BEFORE UPDATE ON entries
WHEN EXISTS (SELECT 1 FROM obligations o JOIN invoices i ON i.id=o.invoice_id WHERE o.entry_id=OLD.id AND i.status='closed')
BEGIN SELECT RAISE(ABORT, 'entry in closed invoice is immutable'); END
""",
    "closed_invoice_update": """
CREATE TRIGGER closed_invoice_update BEFORE UPDATE ON invoices
WHEN OLD.status='closed'
BEGIN SELECT RAISE(ABORT, 'closed invoice is immutable'); END
""",
    "closed_obligation_delete": """
CREATE TRIGGER closed_obligation_delete BEFORE DELETE ON obligations
WHEN EXISTS (SELECT 1 FROM invoices WHERE id=OLD.invoice_id AND status='closed')
BEGIN SELECT RAISE(ABORT, 'closed invoice installment is immutable'); END
""",
    "closed_entry_delete": """
CREATE TRIGGER closed_entry_delete BEFORE DELETE ON entries
WHEN EXISTS (SELECT 1 FROM obligations o JOIN invoices i ON i.id=o.invoice_id WHERE o.entry_id=OLD.id AND i.status='closed')
BEGIN SELECT RAISE(ABORT, 'entry in closed invoice is immutable'); END
""",
    "closed_invoice_delete": """
CREATE TRIGGER closed_invoice_delete BEFORE DELETE ON invoices
WHEN OLD.status='closed'
BEGIN SELECT RAISE(ABORT, 'closed invoice is immutable'); END
""",
    "used_rule_update": """
CREATE TRIGGER used_rule_update BEFORE UPDATE ON card_rules
WHEN EXISTS (SELECT 1 FROM entries WHERE card_rule_id=OLD.id)
BEGIN SELECT RAISE(ABORT, 'used card rule is immutable'); END
""",
    "used_invoice_dates": """
CREATE TRIGGER used_invoice_dates BEFORE UPDATE OF card_id, reference_month, closing_date, due_date ON invoices
WHEN EXISTS (SELECT 1 FROM obligations WHERE invoice_id=OLD.id)
BEGIN SELECT RAISE(ABORT, 'used invoice dates are immutable'); END
""",
    "entry_schedule_update": """
CREATE TRIGGER entry_schedule_update BEFORE UPDATE OF total_cents, installment_count, occurred_on, payment_method, card_id, card_rule_id ON entries
WHEN EXISTS (SELECT 1 FROM obligations WHERE entry_id=OLD.id)
BEGIN SELECT RAISE(ABORT, 'rebuild open schedule explicitly before changing its basis'); END
""",
    "entry_request_key_update": """
CREATE TRIGGER entry_request_key_update BEFORE UPDATE OF request_key ON entries
WHEN NEW.request_key != OLD.request_key
BEGIN SELECT RAISE(ABORT, 'operation key is immutable'); END
""",
    "used_card_holder_update": """
CREATE TRIGGER used_card_holder_update BEFORE UPDATE OF holder_id ON cards
WHEN NEW.holder_id != OLD.holder_id AND EXISTS (SELECT 1 FROM entries WHERE card_id=OLD.id)
BEGIN SELECT RAISE(ABORT, 'holder of used card is immutable'); END
""",
}

def upgrade():
    for statement in TRIGGERS.values():
        op.execute(statement)

def downgrade():
    for name in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER {name}")
