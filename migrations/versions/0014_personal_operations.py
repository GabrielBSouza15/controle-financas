"""Indicador independente e recibos de pagamento integral, sem alterar origens."""
from alembic import op
import sqlalchemy as sa
revision = "0014_personal_operations"
down_revision = "0013_parent_budget"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("personal_operations",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("request_key",sa.String(128),nullable=False,unique=True),
        sa.Column("kind",sa.String(32),nullable=False),
        sa.Column("payload_json",sa.String(),nullable=False),
        sa.Column("result_json",sa.String(),nullable=False),
        sa.Column("recorded_at",sa.String(32),nullable=False),
        sa.CheckConstraint("kind IN ('reinvestment','bill_payment')",name="personal_kind"))
    for action in ("UPDATE","DELETE"):
        op.execute(f"CREATE TRIGGER personal_no_{action.lower()} BEFORE {action} ON personal_operations BEGIN SELECT RAISE(ABORT,'personal operation is immutable'); END")

def downgrade():
    if op.get_bind().exec_driver_sql("SELECT count(*) FROM personal_operations").scalar():
        raise RuntimeError("Preservar histórico; restaurar para arquivo novo.")
    for action in ("update","delete"):
        op.execute("DROP TRIGGER personal_no_"+action)
    op.drop_table("personal_operations")
