"""Recorrências mensais, versões e decisões por ocorrência.
Revision ID: 0006_recurrences
"""
from alembic import op
import sqlalchemy as sa
revision="0006_recurrences"
down_revision="0005_adjustments"
branch_labels=None
depends_on=None
TABLES=["recurrence_series","recurrence_changes","recurrence_occurrences"]
def audit():
    return [sa.Column("id",sa.Integer(),primary_key=True),sa.Column("request_key",sa.String(128),nullable=False),
        sa.Column("reason",sa.String(1000),nullable=False),sa.Column("recorded_at",sa.String(32),nullable=False),
        sa.Column("payload_json",sa.String(),nullable=False),sa.UniqueConstraint("request_key"),
        sa.CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128",name="request_key"),
        sa.CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000",name="reason")]
TRIGGERS={
"recurrence_change_validate":"""CREATE TRIGGER recurrence_change_validate BEFORE INSERT ON recurrence_changes WHEN NEW.effective_month<date((SELECT start_on FROM recurrence_series WHERE id=NEW.series_id),'start of month') BEGIN SELECT RAISE(ABORT,'change precedes series'); END""",
"recurrence_occurrence_validate":"""CREATE TRIGGER recurrence_occurrence_validate BEFORE INSERT ON recurrence_occurrences BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM recurrence_series s WHERE s.id=NEW.series_id AND NEW.scheduled_on>=s.start_on AND (s.end_on IS NULL OR NEW.scheduled_on<=s.end_on)) THEN RAISE(ABORT,'occurrence outside series') END;
 SELECT CASE WHEN NEW.status='confirmed' AND NOT EXISTS(SELECT 1 FROM entries e JOIN recurrence_series s ON s.id=NEW.series_id WHERE e.id=NEW.entry_id AND e.request_key=NEW.request_key AND e.total_cents=NEW.actual_cents AND e.installment_count=1 AND e.kind=s.kind AND e.payment_method=s.payment_method AND e.card_id IS s.card_id AND e.account_id IS s.account_id AND e.category_id IS s.category_id AND e.responsible_id=s.responsible_id AND e.occurred_on<=date('now','-3 hours') AND strftime('%Y-%m',e.occurred_on)=strftime('%Y-%m',NEW.reference_month) AND NOT EXISTS(SELECT 1 FROM entry_changes WHERE source_entry_id=e.id)) THEN RAISE(ABORT,'confirmation does not match entry') END;
END""",
"recurrence_account_owner":"""CREATE TRIGGER recurrence_account_owner BEFORE UPDATE OF owner_id ON accounts WHEN NEW.owner_id!=OLD.owner_id AND EXISTS(SELECT 1 FROM recurrence_series WHERE account_id=OLD.id) BEGIN SELECT RAISE(ABORT,'recurrence account ownership is immutable'); END""",
"recurrence_responsible_role":"""CREATE TRIGGER recurrence_responsible_role BEFORE UPDATE OF code ON responsibles WHEN NEW.code!=OLD.code AND EXISTS(SELECT 1 FROM recurrence_series WHERE responsible_id=OLD.id) BEGIN SELECT RAISE(ABORT,'recurrence role is immutable'); END""",
}
for table in TABLES:
    for action in ("UPDATE","DELETE"):
        name=f"{table}_no_{action.lower()}"
        TRIGGERS[name]=f"CREATE TRIGGER {name} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'recurrence history is immutable'); END"
def upgrade():
    op.create_table("recurrence_series",*audit(),sa.Column("description",sa.String(500),nullable=False),sa.Column("kind",sa.String(16),nullable=False),
        sa.Column("amount_cents",sa.Integer(),nullable=False),sa.Column("start_on",sa.Date(),nullable=False),sa.Column("end_on",sa.Date()),
        sa.Column("payment_method",sa.String(16),nullable=False),
        sa.Column("responsible_id",sa.Integer(),sa.ForeignKey("responsibles.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("category_id",sa.Integer(),sa.ForeignKey("categories.id",ondelete="RESTRICT")),
        sa.Column("account_id",sa.Integer(),sa.ForeignKey("accounts.id",ondelete="RESTRICT")),
        sa.Column("card_id",sa.Integer(),sa.ForeignKey("cards.id",ondelete="RESTRICT")),
        sa.CheckConstraint("length(trim(description)) BETWEEN 1 AND 500",name="description"),
        sa.CheckConstraint("kind IN ('expense','income') AND payment_method IN ('credit','pix','cash')",name="kind_method"),
        sa.CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991",name="amount"),
        sa.CheckConstraint("end_on IS NULL OR end_on>=start_on",name="end"),
        sa.CheckConstraint("(payment_method='credit' AND card_id IS NOT NULL AND account_id IS NULL AND kind='expense') OR (payment_method!='credit' AND card_id IS NULL)",name="links"))
    op.create_table("recurrence_changes",*audit(),sa.Column("series_id",sa.Integer(),sa.ForeignKey("recurrence_series.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("effective_month",sa.Date(),nullable=False),sa.Column("amount_cents",sa.Integer()),sa.Column("suspended",sa.Boolean(create_constraint=True,name="suspended")),
        sa.CheckConstraint("strftime('%d',effective_month)='01'",name="month"),
        sa.CheckConstraint("amount_cents IS NOT NULL OR suspended IS NOT NULL",name="change"),
        sa.CheckConstraint("amount_cents IS NULL OR (typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991)",name="amount"))
    op.create_table("recurrence_occurrences",*audit(),sa.Column("series_id",sa.Integer(),sa.ForeignKey("recurrence_series.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("reference_month",sa.Date(),nullable=False),sa.Column("scheduled_on",sa.Date(),nullable=False),sa.Column("expected_cents",sa.Integer(),nullable=False),
        sa.Column("actual_cents",sa.Integer()),sa.Column("status",sa.String(16),nullable=False),
        sa.Column("entry_id",sa.Integer(),sa.ForeignKey("entries.id",ondelete="RESTRICT")),sa.UniqueConstraint("entry_id"),sa.UniqueConstraint("series_id","reference_month"),
        sa.CheckConstraint("strftime('%d',reference_month)='01' AND strftime('%Y-%m',reference_month)=strftime('%Y-%m',scheduled_on)",name="month"),
        sa.CheckConstraint("(status='confirmed' AND entry_id IS NOT NULL AND actual_cents IS NOT NULL) OR (status='skipped' AND entry_id IS NULL AND actual_cents IS NULL)",name="status"),
        sa.CheckConstraint("typeof(expected_cents)='integer' AND expected_cents BETWEEN 1 AND 9007199254740991",name="expected"),
        sa.CheckConstraint("actual_cents IS NULL OR (typeof(actual_cents)='integer' AND actual_cents BETWEEN 1 AND 9007199254740991)",name="actual"))
    for sql in TRIGGERS.values():op.execute(sql)
def downgrade():
    if any(op.get_bind().exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar() for table in TABLES):
        raise RuntimeError("Reversão bloqueada: preservar histórico financeiro das recorrências.")
    for name in reversed(TRIGGERS):op.execute(f"DROP TRIGGER {name}")
    for table in reversed(TABLES):op.drop_table(table)
