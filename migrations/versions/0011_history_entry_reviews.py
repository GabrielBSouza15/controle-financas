"""Conferência explícita de coincidência entre lançamento novo e histórico."""
from alembic import op
import sqlalchemy as sa
revision = "0011_history_entry_reviews"
down_revision = "0010_history_events"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("history_entry_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("entries.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("matches_json", sa.String(), nullable=False),
        sa.Column("recorded_at", sa.String(32), nullable=False),
        sa.UniqueConstraint("entry_id"),
        sa.CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000", name="reason"),
        sa.CheckConstraint("json_valid(matches_json) AND json_type(matches_json)='array' AND json_array_length(matches_json)>0", name="matches"))
    for action in ("UPDATE", "DELETE"):
        op.execute(f"CREATE TRIGGER history_entry_reviews_no_{action.lower()} BEFORE {action} ON history_entry_reviews BEGIN SELECT RAISE(ABORT,'history entry review is immutable'); END")

def downgrade():
    if op.get_bind().exec_driver_sql("SELECT COUNT(*) FROM history_entry_reviews").scalar():
        raise RuntimeError("Preservar conferências; restaurar outro arquivo.")
    for action in ("UPDATE", "DELETE"):
        op.execute(f"DROP TRIGGER history_entry_reviews_no_{action.lower()}")
    op.drop_table("history_entry_reviews")
