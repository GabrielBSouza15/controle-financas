"""Histórico observado e evidência de pagamento sem data, somente em candidato.
Revision ID: 0009_history_candidate
"""
from alembic import op
import sqlalchemy as sa
revision="0009_history_candidate"
down_revision="0008_invoice_credits"
branch_labels=None
depends_on=None

TRIGGERS={
"history_batch_insert":"""CREATE TRIGGER history_batch_insert BEFORE INSERT ON history_batches WHEN NEW.posted!=0 BEGIN SELECT RAISE(ABORT,'history batch must start unconfirmed'); END""",
"history_record_insert":"""CREATE TRIGGER history_record_insert BEFORE INSERT ON history_records WHEN NOT EXISTS(SELECT 1 FROM history_batches WHERE id=NEW.batch_id AND posted=0) BEGIN SELECT RAISE(ABORT,'history batch already sealed or missing'); END""",
"history_batch_seal":"""CREATE TRIGGER history_batch_seal BEFORE UPDATE ON history_batches BEGIN
 SELECT CASE WHEN OLD.posted!=0 OR NEW.posted!=1 OR NEW.id IS NOT OLD.id OR NEW.source_sha256 IS NOT OLD.source_sha256 OR NEW.payload_sha256 IS NOT OLD.payload_sha256 OR NEW.policy IS NOT OLD.policy OR NEW.recorded_at IS NOT OLD.recorded_at OR NEW.record_count IS NOT OLD.record_count OR NEW.obligation_cents IS NOT OLD.obligation_cents OR NEW.pending_cents IS NOT OLD.pending_cents OR NEW.decisions_json IS NOT OLD.decisions_json THEN RAISE(ABORT,'history is immutable except initial seal') END;
 SELECT CASE WHEN NEW.record_count!=(SELECT COUNT(*) FROM history_records WHERE batch_id=NEW.id) OR NEW.obligation_cents!=COALESCE((SELECT SUM(amount_cents) FROM history_records WHERE batch_id=NEW.id AND role='obligation'),0) OR NEW.pending_cents!=COALESCE((SELECT SUM(amount_cents) FROM history_records WHERE batch_id=NEW.id AND role='obligation' AND paid_evidence=0),0) THEN RAISE(ABORT,'history control totals mismatch') END;
END""",
}
for table in ("history_batches","history_records"):
    for action in ("UPDATE","DELETE"):
        if table=="history_batches" and action=="UPDATE":continue
        name=table+"_no_"+action.lower()
        TRIGGERS[name]=f"CREATE TRIGGER {name} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'history is immutable'); END"

def upgrade():
    op.create_table("history_batches",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("source_sha256",sa.String(64),nullable=False),
        sa.Column("payload_sha256",sa.String(64),nullable=False),
        sa.Column("policy",sa.String(100),nullable=False),
        sa.Column("recorded_at",sa.String(32),nullable=False),
        sa.Column("record_count",sa.Integer(),nullable=False),
        sa.Column("obligation_cents",sa.Integer(),nullable=False),
        sa.Column("pending_cents",sa.Integer(),nullable=False),
        sa.Column("decisions_json",sa.String(),nullable=False),
        sa.Column("posted",sa.Boolean(create_constraint=True,name="posted"),server_default="0",nullable=False),
        sa.UniqueConstraint("source_sha256"),
        sa.CheckConstraint("length(source_sha256)=64 AND source_sha256 NOT GLOB '*[^0-9a-f]*' AND length(payload_sha256)=64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'",name="hashes"),
        sa.CheckConstraint("typeof(record_count)='integer' AND record_count>0",name="count"),
        sa.CheckConstraint("typeof(obligation_cents)='integer' AND obligation_cents BETWEEN 0 AND 9007199254740991 AND typeof(pending_cents)='integer' AND pending_cents BETWEEN 0 AND obligation_cents",name="totals"))
    op.create_table("history_records",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("batch_id",sa.Integer(),sa.ForeignKey("history_batches.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("source_row",sa.Integer(),nullable=False),
        sa.Column("role",sa.String(20),nullable=False),
        sa.Column("description",sa.String(500)),
        sa.Column("kind",sa.String(16),nullable=False),
        sa.Column("amount_cents",sa.Integer(),nullable=False),
        sa.Column("reference_on",sa.Date(),nullable=False),
        sa.Column("observed_number",sa.Integer(),nullable=False),
        sa.Column("observed_count",sa.Integer(),nullable=False),
        sa.Column("responsible_code",sa.String(16),nullable=False),
        sa.Column("payment_method",sa.String(16),nullable=False),
        sa.Column("card_code",sa.String(16)),
        sa.Column("simulated_cycle",sa.Date()),
        sa.Column("category_original",sa.String(100)),
        sa.Column("paid_evidence",sa.Boolean(create_constraint=True,name="paid_evidence"),nullable=False),
        sa.Column("group_key",sa.String(100)),
        sa.Column("group_state",sa.String(16),nullable=False),
        sa.Column("evidence_json",sa.String(),nullable=False),
        sa.UniqueConstraint("batch_id","source_row"),
        sa.CheckConstraint("typeof(source_row)='integer' AND source_row BETWEEN 1 AND 1048576",name="source_row"),
        sa.CheckConstraint("role IN ('obligation','opening_balance','excluded')",name="role"),
        sa.CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991",name="amount"),
        sa.CheckConstraint("typeof(observed_number)='integer' AND typeof(observed_count)='integer' AND observed_number BETWEEN 1 AND observed_count",name="parts"),
        sa.CheckConstraint("kind IN ('expense','income') AND responsible_code IN ('self','parents') AND payment_method IN ('credit','pix','cash')",name="classification"),
        sa.CheckConstraint("(payment_method='credit' AND kind='expense' AND card_code IN ('nubank','mercado_pago') AND simulated_cycle IS NOT NULL) OR (payment_method!='credit' AND card_code IS NULL AND simulated_cycle IS NULL)",name="card"),
        sa.CheckConstraint("group_state IN ('confirmed','candidate','unmapped') AND ((group_state='unmapped' AND group_key IS NULL) OR (group_state!='unmapped' AND group_key IS NOT NULL))",name="group"),
        sa.CheckConstraint("role!='opening_balance' OR (payment_method!='credit' AND responsible_code='self' AND observed_number=1 AND observed_count=1)",name="opening"))
    for sql in TRIGGERS.values():op.execute(sql)

def downgrade():
    if op.get_bind().exec_driver_sql("SELECT COUNT(*) FROM history_batches").scalar():
        raise RuntimeError("Preservar o candidato e sua evidência; restaure outro arquivo.")
    for name in reversed(TRIGGERS):op.execute(f"DROP TRIGGER {name}")
    op.drop_table("history_records")
    op.drop_table("history_batches")
