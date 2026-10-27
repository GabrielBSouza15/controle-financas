"""Pagamento imediato informado sem atribuir conta ou criar evento de caixa."""
from alembic import op
import sqlalchemy as sa

revision = "0015_entry_payment_marks"
down_revision = "0014_personal_operations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("entry_payment_marks",
        sa.Column("entry_id",sa.Integer(),sa.ForeignKey("entries.id",ondelete="RESTRICT"),primary_key=True),
        sa.Column("recorded_at",sa.String(32),nullable=False),
        sa.CheckConstraint("length(trim(recorded_at)) > 0",name="recorded"))
    op.execute("""CREATE TRIGGER entry_payment_mark_validate BEFORE INSERT ON entry_payment_marks
    WHEN NOT EXISTS (SELECT 1 FROM entries e JOIN responsibles r ON r.id=e.responsible_id
      WHERE e.id=NEW.entry_id AND e.kind='expense' AND e.payment_method IN ('pix','cash')
      AND r.code='self' AND e.account_id IS NULL
      AND NOT EXISTS (SELECT 1 FROM entry_changes WHERE source_entry_id=e.id)
      AND NOT EXISTS (SELECT 1 FROM settlement_allocations a JOIN obligations o ON o.id=a.obligation_id WHERE o.entry_id=e.id)
      AND NOT EXISTS (SELECT 1 FROM obligation_adjustments a JOIN obligations o ON o.id=a.obligation_id WHERE o.entry_id=e.id))
    BEGIN SELECT RAISE(ABORT,'payment mark requires an unlinked own non-card expense'); END""")
    for action in ("UPDATE","DELETE"):
        op.execute(f"CREATE TRIGGER entry_payment_mark_no_{action.lower()} BEFORE {action} ON entry_payment_marks BEGIN SELECT RAISE(ABORT,'payment mark is immutable'); END")
    op.execute("""CREATE TRIGGER entry_payment_mark_change BEFORE INSERT ON entry_changes
    WHEN EXISTS (SELECT 1 FROM entry_payment_marks WHERE entry_id=NEW.source_entry_id)
    BEGIN SELECT RAISE(ABORT,'paid entry must preserve payment evidence'); END""")
    op.execute("""CREATE TRIGGER entry_payment_mark_allocation BEFORE INSERT ON settlement_allocations
    WHEN EXISTS (SELECT 1 FROM entry_payment_marks m JOIN obligations o ON o.entry_id=m.entry_id WHERE o.id=NEW.obligation_id)
    BEGIN SELECT RAISE(ABORT,'expense already marked paid without account'); END""")


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT count(*) FROM entry_payment_marks").scalar():
        raise RuntimeError("Preservar indicações de pagamento; restaurar para arquivo novo.")
    for name in ("validate","no_update","no_delete","change","allocation"):
        op.execute("DROP TRIGGER entry_payment_mark_"+name)
    op.drop_table("entry_payment_marks")
