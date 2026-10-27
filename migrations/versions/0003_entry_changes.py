"""Histórico imutável de substituições e cancelamentos, sem apagar parcelas.

Revision ID: 0003_entry_changes
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_entry_changes"
down_revision = "0002_integrity"
branch_labels = None
depends_on = None

TRIGGERS = {
    "change_validate": """CREATE TRIGGER change_validate BEFORE INSERT ON entry_changes BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM obligations o JOIN invoices i ON i.id=o.invoice_id WHERE o.entry_id=NEW.source_entry_id AND i.status='closed') THEN RAISE(ABORT, 'closed source cannot change') END;
 SELECT CASE WHEN NEW.action='replace' AND NOT EXISTS(SELECT 1 FROM entries e WHERE e.id=NEW.replacement_entry_id AND e.request_key=NEW.request_key) THEN RAISE(ABORT, 'replacement must use action key') END;
 SELECT CASE WHEN NEW.action='replace' AND EXISTS(SELECT 1 FROM entry_changes WHERE source_entry_id=NEW.replacement_entry_id) THEN RAISE(ABORT, 'replacement must be active') END;
 SELECT CASE WHEN (SELECT COUNT(*) FROM obligations WHERE entry_id=NEW.source_entry_id)!=(SELECT installment_count FROM entries WHERE id=NEW.source_entry_id) OR (SELECT SUM(amount_cents) FROM obligations WHERE entry_id=NEW.source_entry_id)!=(SELECT total_cents FROM entries WHERE id=NEW.source_entry_id) THEN RAISE(ABORT, 'incomplete source schedule') END;
 SELECT CASE WHEN NEW.action='replace' AND ((SELECT COUNT(*) FROM obligations WHERE entry_id=NEW.replacement_entry_id)!=(SELECT installment_count FROM entries WHERE id=NEW.replacement_entry_id) OR (SELECT SUM(amount_cents) FROM obligations WHERE entry_id=NEW.replacement_entry_id)!=(SELECT total_cents FROM entries WHERE id=NEW.replacement_entry_id)) THEN RAISE(ABORT, 'incomplete replacement schedule') END;
END""",
    "change_no_update": """CREATE TRIGGER change_no_update BEFORE UPDATE ON entry_changes BEGIN SELECT RAISE(ABORT, 'change history is immutable'); END""",
    "change_no_delete": """CREATE TRIGGER change_no_delete BEFORE DELETE ON entry_changes BEGIN SELECT RAISE(ABORT, 'change history is immutable'); END""",
}
for table, entry_column in [("entries", "id"), ("obligations", "entry_id")]:
    for action in ["UPDATE", "DELETE"]:
        name = f"audited_{table}_{action.lower()}"
        condition = f"EXISTS(SELECT 1 FROM entry_changes WHERE source_entry_id=OLD.{entry_column} OR replacement_entry_id=OLD.{entry_column})"
        if table == "obligations" and action == "UPDATE":
            condition += " OR EXISTS(SELECT 1 FROM entry_changes WHERE source_entry_id=NEW.entry_id OR replacement_entry_id=NEW.entry_id)"
        TRIGGERS[name] = f"CREATE TRIGGER {name} BEFORE {action} ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT, 'audited record is immutable'); END"
TRIGGERS["audited_obligations_insert"] = """CREATE TRIGGER audited_obligations_insert BEFORE INSERT ON obligations WHEN EXISTS(SELECT 1 FROM entry_changes WHERE source_entry_id=NEW.entry_id OR replacement_entry_id=NEW.entry_id) BEGIN SELECT RAISE(ABORT, 'audited schedule is immutable'); END"""


def upgrade():
    op.create_table("entry_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("source_entry_id", sa.Integer(), sa.ForeignKey("entries.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("replacement_entry_id", sa.Integer(), sa.ForeignKey("entries.id", ondelete="RESTRICT")),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("recorded_at", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.String(), nullable=False),
        sa.UniqueConstraint("request_key"), sa.UniqueConstraint("source_entry_id"), sa.UniqueConstraint("replacement_entry_id"),
        sa.CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128", name="request_key"),
        sa.CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000", name="reason"),
        sa.CheckConstraint("(action='replace' AND replacement_entry_id IS NOT NULL AND replacement_entry_id > source_entry_id) OR (action='cancel' AND replacement_entry_id IS NULL)", name="transition"))
    for sql in TRIGGERS.values():
        op.execute(sql)


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT COUNT(*) FROM entry_changes").scalar():
        raise RuntimeError("Reversão bloqueada: preservação do histórico de alterações.")
    for name in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER {name}")
    op.drop_table("entry_changes")
