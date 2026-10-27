"""Indicação do repasse para orçamento; sem reescrever dados antigos."""
from alembic import op
import sqlalchemy as sa
revision = "0013_parent_budget"
down_revision = "0012_history_operations"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("parent_budget_choices",
        sa.Column("entry_id",sa.Integer(),sa.ForeignKey("entries.id",ondelete="RESTRICT"),primary_key=True),
        sa.Column("repayment_status",sa.String(16),nullable=False),
        sa.Column("recorded_at",sa.String(32),nullable=False),
        sa.CheckConstraint("repayment_status IN ('received','pending')",name="status"),
        sa.CheckConstraint("length(trim(recorded_at)) > 0",name="recorded"))
    op.execute("""CREATE TRIGGER parent_budget_eligibility BEFORE INSERT ON parent_budget_choices
    WHEN NOT EXISTS (SELECT 1 FROM entries e JOIN responsibles r ON r.id=e.responsible_id
      WHERE e.id=NEW.entry_id AND e.kind='expense' AND e.payment_method IN ('pix','cash') AND r.code='parents')
    BEGIN SELECT RAISE(ABORT,'parent budget choice requires a non-card parent expense'); END""")
    for action in ("UPDATE","DELETE"):
        op.execute(f"CREATE TRIGGER parent_budget_no_{action.lower()} BEFORE {action} ON parent_budget_choices BEGIN SELECT RAISE(ABORT,'parent budget choice is immutable'); END")

def downgrade():
    if op.get_bind().exec_driver_sql("SELECT count(*) FROM parent_budget_choices").scalar():
        raise RuntimeError("Preservar escolhas orçamentárias; restaurar outro arquivo.")
    for name in ("parent_budget_eligibility","parent_budget_no_update","parent_budget_no_delete"):
        op.execute("DROP TRIGGER "+name)
    op.drop_table("parent_budget_choices")
