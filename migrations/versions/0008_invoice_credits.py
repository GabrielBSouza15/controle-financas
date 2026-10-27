"""Compensação entre faturas e pagamento ao emissor independente do vínculo.
Revision ID: 0008_invoice_credits
"""
from alembic import op
import sqlalchemy as sa
import importlib
revision="0008_invoice_credits"
down_revision="0007_legacy_trial"
branch_labels=None
depends_on=None
TABLES=["invoice_credit_applications","invoice_credit_allocations","invoice_credit_reversals"]
REPLACED=["allocation_capacity_payment","batch_capacity_payment","closure_validate"]
ACTIVE_CREDIT="c.posted=1 AND NOT EXISTS(SELECT 1 FROM invoice_credit_reversals r WHERE r.application_id=c.id)"
CREDIT_USED=f"COALESCE((SELECT SUM(ca.amount_cents) FROM invoice_credit_allocations ca JOIN invoice_credit_applications c ON c.id=ca.application_id WHERE ca.obligation_id=o.id AND {ACTIVE_CREDIT}),0)"
ACTIVE_EVENT="""NOT EXISTS(SELECT 1 FROM event_reversals r WHERE r.event_id=f.id)
AND EXISTS(SELECT 1 FROM allocation_batches b WHERE b.event_id=f.id AND b.request_key=f.request_key AND b.posted=1)"""
NET_AMOUNT="o.amount_cents+COALESCE((SELECT SUM(z.delta_cents) FROM obligation_adjustments z WHERE z.obligation_id=o.id),0)"
OFFSET="COALESCE((SELECT SUM(x.amount_cents) FROM allocation_offsets x JOIN settlement_corrections cc ON cc.id=x.correction_id WHERE x.allocation_id=a.id AND cc.posted=1),0)"
BANK_USED=f"""COALESCE((SELECT SUM(a.amount_cents-({OFFSET})) FROM settlement_allocations a JOIN allocation_batches b ON b.id=a.batch_id JOIN financial_events f ON f.id=b.event_id WHERE a.obligation_id=o.id AND b.posted=1 AND f.kind!='repayment' AND {ACTIVE_EVENT}),0)"""
VIEW=f"""CREATE VIEW invoice_issuer_balances AS SELECT q.*,
 q.obligations_total+q.credit_out-q.credit_in AS statement_total,
 q.obligations_total+q.credit_out-q.credit_in-q.bank_paid AS remaining
FROM (SELECT i.id AS invoice_id,
 COALESCE((SELECT SUM({NET_AMOUNT}) FROM obligations o JOIN entries e ON e.id=o.entry_id WHERE o.invoice_id=i.id AND NOT EXISTS(SELECT 1 FROM entry_changes WHERE source_entry_id=e.id)),0) AS obligations_total,
 COALESCE((SELECT SUM(f.amount_cents-COALESCE((SELECT SUM(sc.amount_cents) FROM settlement_corrections sc WHERE sc.event_id=f.id AND sc.action='refund' AND sc.posted=1),0)) FROM financial_events f WHERE f.invoice_id=i.id AND f.kind='invoice_payment' AND {ACTIVE_EVENT}),0) AS bank_paid,
 COALESCE((SELECT SUM(c.amount_cents) FROM invoice_credit_applications c WHERE c.source_invoice_id=i.id AND {ACTIVE_CREDIT}),0) AS credit_out,
 COALESCE((SELECT SUM(c.amount_cents) FROM invoice_credit_applications c WHERE c.target_invoice_id=i.id AND {ACTIVE_CREDIT}),0) AS credit_in
 FROM invoices i) q"""
TRIGGERS={
"invoice_credit_insert":"""CREATE TRIGGER invoice_credit_insert BEFORE INSERT ON invoice_credit_applications BEGIN
 SELECT CASE WHEN NEW.posted!=0 OR NOT EXISTS(SELECT 1 FROM invoices src JOIN invoices dst ON dst.id=NEW.target_invoice_id WHERE src.id=NEW.source_invoice_id AND src.card_id=dst.card_id AND src.id!=dst.id) OR NEW.occurred_on>date('now','-3 hours') THEN RAISE(ABORT,'credit needs same card, distinct invoices and actual date') END;
END""",
"invoice_credit_allocation_insert":"""CREATE TRIGGER invoice_credit_allocation_insert BEFORE INSERT ON invoice_credit_allocations BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM invoice_credit_applications c JOIN obligations o ON o.id=NEW.obligation_id JOIN entries e ON e.id=o.entry_id WHERE c.id=NEW.application_id AND c.posted=0 AND o.invoice_id=c.target_invoice_id AND e.occurred_on<=c.occurred_on AND NOT EXISTS(SELECT 1 FROM entry_changes WHERE source_entry_id=e.id)) THEN RAISE(ABORT,'credit allocation origin mismatch') END;
 SELECT CASE WHEN NEW.amount_cents>(SELECT amount_cents FROM invoice_credit_applications WHERE id=NEW.application_id)-COALESCE((SELECT SUM(amount_cents) FROM invoice_credit_allocations WHERE application_id=NEW.application_id),0) THEN RAISE(ABORT,'credit allocation exceeds application') END;
END""",
"invoice_credit_seal":f"""CREATE TRIGGER invoice_credit_seal BEFORE UPDATE ON invoice_credit_applications BEGIN
 SELECT CASE WHEN OLD.posted!=0 OR NEW.posted!=1 OR NEW.id IS NOT OLD.id OR NEW.request_key IS NOT OLD.request_key OR NEW.source_invoice_id IS NOT OLD.source_invoice_id OR NEW.target_invoice_id IS NOT OLD.target_invoice_id OR NEW.occurred_on IS NOT OLD.occurred_on OR NEW.amount_cents IS NOT OLD.amount_cents OR NEW.reason IS NOT OLD.reason OR NEW.recorded_at IS NOT OLD.recorded_at OR NEW.payload_json IS NOT OLD.payload_json THEN RAISE(ABORT,'credit application only allows confirmation') END;
 SELECT CASE WHEN NEW.amount_cents>-(SELECT remaining FROM invoice_issuer_balances WHERE invoice_id=NEW.source_invoice_id) OR NEW.amount_cents>(SELECT remaining FROM invoice_issuer_balances WHERE invoice_id=NEW.target_invoice_id) THEN RAISE(ABORT,'credit source or target capacity changed') END;
 SELECT CASE WHEN NEW.amount_cents!=COALESCE((SELECT SUM(amount_cents) FROM invoice_credit_allocations WHERE application_id=NEW.id),0) THEN RAISE(ABORT,'incomplete credit application') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM invoice_credit_allocations current JOIN obligations o ON o.id=current.obligation_id JOIN entries e ON e.id=o.entry_id WHERE current.application_id=NEW.id AND (o.invoice_id!=NEW.target_invoice_id OR e.occurred_on>NEW.occurred_on OR EXISTS(SELECT 1 FROM entry_changes WHERE source_entry_id=e.id) OR current.amount_cents>({NET_AMOUNT})-({BANK_USED})-({CREDIT_USED}))) THEN RAISE(ABORT,'credit allocation capacity changed') END;
 SELECT CASE WHEN NEW.occurred_on<COALESCE((SELECT MAX(d) FROM (
 SELECT a.occurred_on AS d FROM obligation_adjustments a JOIN obligations o ON o.id=a.obligation_id WHERE o.invoice_id=NEW.source_invoice_id
 UNION ALL SELECT occurred_on FROM financial_events WHERE invoice_id=NEW.source_invoice_id
 UNION ALL SELECT c.occurred_on FROM invoice_credit_applications c WHERE c.target_invoice_id=NEW.source_invoice_id AND {ACTIVE_CREDIT})),NEW.occurred_on) THEN RAISE(ABORT,'credit application precedes source evidence') END;
END""",
"invoice_credit_reversal_validate":"""CREATE TRIGGER invoice_credit_reversal_validate BEFORE INSERT ON invoice_credit_reversals WHEN NOT EXISTS(SELECT 1 FROM invoice_credit_applications WHERE id=NEW.application_id AND posted=1) BEGIN SELECT RAISE(ABORT,'only confirmed credit can be reversed'); END""",
"credit_source_event_reversal":f"""CREATE TRIGGER credit_source_event_reversal BEFORE INSERT ON event_reversals WHEN EXISTS(SELECT 1 FROM financial_events f JOIN invoice_credit_applications c ON c.source_invoice_id=f.invoice_id WHERE f.id=NEW.event_id AND {ACTIVE_CREDIT}) BEGIN SELECT RAISE(ABORT,'credit application protects source payment'); END""",
"credited_entry_change":"""CREATE TRIGGER credited_entry_change BEFORE INSERT ON entry_changes WHEN EXISTS(SELECT 1 FROM invoice_credit_allocations a JOIN obligations o ON o.id=a.obligation_id WHERE o.entry_id=NEW.source_entry_id) BEGIN SELECT RAISE(ABORT,'credited history requires explicit adjustment'); END""",
"closure_validate":"""CREATE TRIGGER closure_validate BEFORE INSERT ON invoice_closures BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM invoices WHERE id=NEW.invoice_id AND status='open') THEN RAISE(ABORT,'invoice closed or missing') END;
 SELECT CASE WHEN NEW.total_cents!=(SELECT statement_total FROM invoice_issuer_balances WHERE invoice_id=NEW.invoice_id) THEN RAISE(ABORT,'statement total changed') END;
END""",
}
TRIGGERS["issuer_payment_capacity"]="""CREATE TRIGGER issuer_payment_capacity BEFORE UPDATE ON allocation_batches WHEN NEW.posted=1 AND EXISTS(SELECT 1 FROM financial_events f WHERE f.id=NEW.event_id AND f.kind='invoice_payment' AND f.request_key=NEW.request_key AND f.amount_cents>(SELECT remaining FROM invoice_issuer_balances WHERE invoice_id=f.invoice_id)) BEGIN SELECT RAISE(ABORT,'payment exceeds issuer balance'); END"""
previous=importlib.import_module("migrations.versions.0005_adjustments")
for name in ["allocation_capacity_payment","batch_capacity_payment"]:
    # A revisão 0005 é congelada; só esta revisão passa a descontar créditos não monetários.
    TRIGGERS[name]=previous.TRIGGERS[name].replace(previous.ADJUSTED,previous.ADJUSTED+"-("+CREDIT_USED+")")
for table in TABLES:
    for action in ("UPDATE","DELETE"):
        if table=="invoice_credit_applications" and action=="UPDATE":continue
        name=f"{table}_no_{action.lower()}"
        TRIGGERS[name]=f"CREATE TRIGGER {name} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'issuer credit history is immutable'); END"
for table,column in [("entries","id"),("obligations","entry_id")]:
    for action in ("UPDATE","DELETE"):
        name=f"credited_{table}_{action.lower()}"
        TRIGGERS[name]=f"CREATE TRIGGER {name} BEFORE {action} ON {table} WHEN EXISTS(SELECT 1 FROM invoice_credit_allocations a JOIN obligations o ON o.id=a.obligation_id WHERE o.entry_id=OLD.{column}) BEGIN SELECT RAISE(ABORT,'credited history is immutable'); END"
TRIGGERS["credited_obligation_insert"]="CREATE TRIGGER credited_obligation_insert BEFORE INSERT ON obligations WHEN EXISTS(SELECT 1 FROM invoice_credit_allocations a JOIN obligations o ON o.id=a.obligation_id WHERE o.entry_id=NEW.entry_id) BEGIN SELECT RAISE(ABORT,'credited schedule is immutable'); END"
def audit():
    return [sa.Column("id",sa.Integer(),primary_key=True),sa.Column("request_key",sa.String(128),nullable=False),sa.Column("reason",sa.String(1000),nullable=False),sa.Column("recorded_at",sa.String(32),nullable=False),sa.Column("payload_json",sa.String(),nullable=False),sa.UniqueConstraint("request_key"),sa.CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128",name="request_key"),sa.CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000",name="reason")]
def money():
    return sa.CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991",name="amount")
def upgrade():
    op.create_table("invoice_credit_applications",*audit(),sa.Column("source_invoice_id",sa.Integer(),sa.ForeignKey("invoices.id",ondelete="RESTRICT"),nullable=False),sa.Column("target_invoice_id",sa.Integer(),sa.ForeignKey("invoices.id",ondelete="RESTRICT"),nullable=False),sa.Column("occurred_on",sa.Date(),nullable=False),sa.Column("amount_cents",sa.Integer(),nullable=False),sa.Column("posted",sa.Boolean(create_constraint=True,name="posted"),server_default="0",nullable=False),money(),sa.CheckConstraint("source_invoice_id!=target_invoice_id",name="invoices"))
    op.create_table("invoice_credit_allocations",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("application_id",sa.Integer(),sa.ForeignKey("invoice_credit_applications.id",ondelete="RESTRICT"),nullable=False),sa.Column("obligation_id",sa.Integer(),sa.ForeignKey("obligations.id",ondelete="RESTRICT"),nullable=False),sa.Column("amount_cents",sa.Integer(),nullable=False),money(),sa.UniqueConstraint("application_id","obligation_id"))
    op.create_table("invoice_credit_reversals",*audit(),sa.Column("application_id",sa.Integer(),sa.ForeignKey("invoice_credit_applications.id",ondelete="RESTRICT"),nullable=False),sa.UniqueConstraint("application_id"))
    op.execute(VIEW)
    for name in REPLACED:op.execute(f"DROP TRIGGER {name}")
    for sql in TRIGGERS.values():op.execute(sql)
def downgrade():
    if any(op.get_bind().exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar() for table in TABLES):
        raise RuntimeError("Reversão bloqueada: preservar histórico financeiro de créditos.")
    for name in reversed(TRIGGERS):op.execute(f"DROP TRIGGER {name}")
    op.execute("DROP VIEW invoice_issuer_balances")
    for table in reversed(TABLES):op.drop_table(table)
    for name in REPLACED:op.execute(previous.TRIGGERS[name])
