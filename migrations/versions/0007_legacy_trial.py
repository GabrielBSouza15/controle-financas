"""Origem imutável e obrigações observadas em ensaio separado.
Revision ID: 0007_legacy_trial
"""
from alembic import op
import sqlalchemy as sa
revision="0007_legacy_trial"
down_revision="0006_recurrences"
branch_labels=None
depends_on=None
TABLES=["legacy_import_batches","legacy_source_rows","legacy_obligations"]
TRIGGERS={}
for table in TABLES:
    for action in ("UPDATE","DELETE"):
        name=f"{table}_no_{action.lower()}"
        TRIGGERS[name]=f"CREATE TRIGGER {name} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'legacy evidence is immutable'); END"
TRIGGERS["legacy_obligation_validate"]="""CREATE TRIGGER legacy_obligation_validate BEFORE INSERT ON legacy_obligations WHEN NOT EXISTS(SELECT 1 FROM legacy_source_rows WHERE id=NEW.source_row_id AND status='accepted') BEGIN SELECT RAISE(ABORT,'only accepted observed obligations can be mapped'); END"""
def upgrade():
    op.create_table("legacy_import_batches",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("file_sha256",sa.String(64),nullable=False),sa.Column("policy_version",sa.String(100),nullable=False),sa.Column("original_name",sa.String(500),nullable=False),sa.Column("recorded_at",sa.String(32),nullable=False),sa.Column("extraction_sha256",sa.String(64),nullable=False),sa.Column("control_json",sa.String(),nullable=False),sa.Column("context_json",sa.String(),nullable=False),sa.UniqueConstraint("file_sha256","policy_version"),sa.CheckConstraint("length(file_sha256)=64 AND file_sha256 NOT GLOB '*[^0-9a-f]*'",name="hash"),sa.CheckConstraint("length(extraction_sha256)=64 AND extraction_sha256 NOT GLOB '*[^0-9a-f]*'",name="extraction_hash"))
    op.create_table("legacy_source_rows",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("batch_id",sa.Integer(),sa.ForeignKey("legacy_import_batches.id",ondelete="RESTRICT"),nullable=False),sa.Column("sheet_name",sa.String(100),nullable=False),sa.Column("row_number",sa.Integer(),nullable=False),sa.Column("status",sa.String(16),nullable=False),sa.Column("source_amount_decimal",sa.String(128)),sa.Column("raw_json",sa.String(),nullable=False),sa.Column("issues_json",sa.String(),nullable=False),sa.Column("proposal_json",sa.String(),nullable=False),sa.UniqueConstraint("batch_id","sheet_name","row_number"),sa.CheckConstraint("typeof(row_number)='integer' AND row_number BETWEEN 1 AND 1048576",name="row"),sa.CheckConstraint("status IN ('accepted','pending','excluded')",name="status"))
    op.create_table("legacy_obligations",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("source_row_id",sa.Integer(),sa.ForeignKey("legacy_source_rows.id",ondelete="RESTRICT"),nullable=False),sa.Column("reference_on",sa.Date(),nullable=False),sa.Column("description",sa.String(500)),sa.Column("normalized_description",sa.String(500),nullable=False),sa.Column("amount_cents",sa.Integer(),nullable=False),sa.Column("observed_number",sa.Integer(),nullable=False),sa.Column("observed_count",sa.Integer(),nullable=False),sa.Column("kind",sa.String(16),nullable=False),sa.Column("responsible_code",sa.String(16),nullable=False),sa.Column("payment_method",sa.String(16),nullable=False),sa.Column("card_code",sa.String(16)),sa.Column("bank_original",sa.String(100)),sa.Column("category_original",sa.String(100)),sa.Column("paid_evidence",sa.Boolean(create_constraint=True,name="paid_evidence"),nullable=False),sa.Column("simulated_cycle",sa.Date()),sa.UniqueConstraint("source_row_id"),sa.CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991",name="amount"),sa.CheckConstraint("typeof(observed_number)='integer' AND typeof(observed_count)='integer' AND observed_number BETWEEN 1 AND observed_count",name="installments"),sa.CheckConstraint("kind IN ('expense','income') AND responsible_code IN ('self','parents') AND payment_method IN ('credit','pix','cash')",name="classification"),sa.CheckConstraint("(payment_method='credit' AND card_code IN ('nubank','mercado_pago') AND kind='expense' AND simulated_cycle IS NOT NULL) OR (payment_method!='credit' AND card_code IS NULL AND simulated_cycle IS NULL)",name="card"))
    for sql in TRIGGERS.values():op.execute(sql)
def downgrade():
    if any(op.get_bind().exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar() for table in TABLES):
        raise RuntimeError("Reversão bloqueada: preservar histórico financeiro e evidências importadas.")
    for name in reversed(TRIGGERS):op.execute(f"DROP TRIGGER {name}")
    for table in reversed(TABLES):op.drop_table(table)
