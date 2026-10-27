"""Liquidações explícitas de obrigações históricas.
Revision ID: 0010_history_events
"""
from alembic import op
import sqlalchemy as sa
revision="0010_history_events"
down_revision="0009_history_candidate"
branch_labels=None
depends_on=None
ACTIVE="e.posted=1 AND NOT EXISTS(SELECT 1 FROM history_event_reversals x WHERE x.event_id=e.id)"
USED=f"COALESCE((SELECT SUM(a.amount_cents) FROM history_allocations a JOIN history_events e ON e.id=a.event_id WHERE a.record_id=r.id AND e.kind!='repayment' AND {ACTIVE}),0)"
COVERED=f"COALESCE((SELECT SUM(a.amount_cents) FROM history_allocations a JOIN history_events e ON e.id=a.event_id WHERE a.record_id=r.id AND (e.kind='repayment' OR e.payer_code='parents') AND {ACTIVE}),0)"
ELIGIBLE="""r.role='obligation' AND b.posted=1 AND
 ((e.kind='invoice_payment' AND r.kind='expense' AND r.card_code IS NOT NULL) OR
  (e.kind='expense_payment' AND r.kind='expense' AND r.card_code IS NULL) OR
  (e.kind='income_receipt' AND r.kind='income' AND r.responsible_code='self' AND r.card_code IS NULL) OR
  (e.kind='repayment' AND r.kind='expense' AND r.responsible_code='parents'))
 AND (e.payer_code IS NOT 'parents' OR r.responsible_code='parents')"""
CAPACITY=f"""(e.kind='repayment' AND NEW.amount_cents>r.amount_cents-({COVERED})) OR
 (e.kind!='repayment' AND (r.paid_evidence=1 OR NEW.amount_cents>r.amount_cents-({USED}))) OR
 (e.kind!='repayment' AND e.payer_code='parents' AND NEW.amount_cents>r.amount_cents-({COVERED}))"""
TRIGGERS={
"history_event_insert":"""CREATE TRIGGER history_event_insert BEFORE INSERT ON history_events BEGIN
 SELECT CASE WHEN NEW.posted!=0 OR date(NEW.occurred_on,'+0 days') IS NOT NEW.occurred_on OR NEW.occurred_on>date('now','-3 hours') THEN RAISE(ABORT,'history event requires actual date and unposted state') END;
 SELECT CASE WHEN NEW.account_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM accounts a JOIN responsibles p ON p.id=a.owner_id WHERE a.id=NEW.account_id AND p.code='self') THEN RAISE(ABORT,'history cash needs own account') END;
END""",
"history_allocation_insert":f"""CREATE TRIGGER history_allocation_insert BEFORE INSERT ON history_allocations BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM history_events e JOIN history_records r ON r.id=NEW.record_id JOIN history_batches b ON b.id=r.batch_id WHERE e.id=NEW.event_id AND e.posted=0 AND {ELIGIBLE}) THEN RAISE(ABORT,'history allocation is ineligible') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM history_records r JOIN history_events e ON e.id=NEW.event_id WHERE r.id=NEW.record_id AND ({CAPACITY})) THEN RAISE(ABORT,'history allocation exceeds available amount') END;
 SELECT CASE WHEN NEW.amount_cents>(SELECT amount_cents FROM history_events WHERE id=NEW.event_id)-COALESCE((SELECT SUM(amount_cents) FROM history_allocations WHERE event_id=NEW.event_id),0) OR (SELECT COUNT(*) FROM history_allocations WHERE event_id=NEW.event_id)>=100 THEN RAISE(ABORT,'history allocation exceeds event or resource limit') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM history_events e JOIN history_allocations a ON a.event_id=e.id JOIN history_records old ON old.id=a.record_id JOIN history_records new ON new.id=NEW.record_id WHERE e.id=NEW.event_id AND e.kind='invoice_payment' AND (old.card_code IS NOT new.card_code OR old.simulated_cycle IS NOT new.simulated_cycle OR old.batch_id!=new.batch_id)) THEN RAISE(ABORT,'history payment must belong to same card cycle and source') END;
END""",
"history_event_seal":f"""CREATE TRIGGER history_event_seal BEFORE UPDATE ON history_events BEGIN
 SELECT CASE WHEN OLD.posted!=0 OR NEW.posted!=1 OR NEW.id IS NOT OLD.id OR NEW.request_key IS NOT OLD.request_key OR NEW.kind IS NOT OLD.kind OR NEW.occurred_on IS NOT OLD.occurred_on OR NEW.amount_cents IS NOT OLD.amount_cents OR NEW.payer_code IS NOT OLD.payer_code OR NEW.account_id IS NOT OLD.account_id OR NEW.reason IS NOT OLD.reason OR NEW.recorded_at IS NOT OLD.recorded_at OR NEW.payload_json IS NOT OLD.payload_json THEN RAISE(ABORT,'history event only allows initial seal') END;
 SELECT CASE WHEN NEW.amount_cents!=COALESCE((SELECT SUM(amount_cents) FROM history_allocations WHERE event_id=NEW.id),0) THEN RAISE(ABORT,'history allocations must match effective event') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM history_allocations cur JOIN history_records r ON r.id=cur.record_id JOIN history_events e ON e.id=cur.event_id WHERE e.id=NEW.id AND ((e.kind='repayment' AND cur.amount_cents>r.amount_cents-({COVERED})) OR (e.kind!='repayment' AND (r.paid_evidence=1 OR cur.amount_cents>r.amount_cents-({USED}))) OR (e.kind!='repayment' AND e.payer_code='parents' AND cur.amount_cents>r.amount_cents-({COVERED})))) THEN RAISE(ABORT,'history capacity changed before seal') END;
END""",
"history_reversal_insert":"""CREATE TRIGGER history_reversal_insert BEFORE INSERT ON history_event_reversals WHEN NOT EXISTS(SELECT 1 FROM history_events WHERE id=NEW.event_id AND posted=1) BEGIN SELECT RAISE(ABORT,'only confirmed history event can be annulled'); END""",
"history_account_owner":"""CREATE TRIGGER history_account_owner BEFORE UPDATE OF owner_id ON accounts WHEN NEW.owner_id!=OLD.owner_id AND EXISTS(SELECT 1 FROM history_events WHERE account_id=OLD.id) BEGIN SELECT RAISE(ABORT,'history protects cash account owner'); END""",
"history_responsible_role":"""CREATE TRIGGER history_responsible_role BEFORE UPDATE OF code ON responsibles WHEN NEW.code!=OLD.code AND EXISTS(SELECT 1 FROM accounts a JOIN history_events e ON e.account_id=a.id WHERE a.owner_id=OLD.id) BEGIN SELECT RAISE(ABORT,'history protects account financial role'); END""",
}
for table in ("history_events","history_allocations","history_event_reversals"):
 for action in ("UPDATE","DELETE"):
  if table=="history_events" and action=="UPDATE":continue
  TRIGGERS[f"{table}_no_{action.lower()}"]=f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'history financial evidence is immutable'); END"

def upgrade():
 op.create_table("history_events",
  sa.Column("id",sa.Integer(),primary_key=True),sa.Column("request_key",sa.String(128),nullable=False),
  sa.Column("kind",sa.String(24),nullable=False),sa.Column("occurred_on",sa.Date(),nullable=False),
  sa.Column("amount_cents",sa.Integer(),nullable=False),sa.Column("payer_code",sa.String(16)),
  sa.Column("account_id",sa.Integer(),sa.ForeignKey("accounts.id",ondelete="RESTRICT")),
  sa.Column("reason",sa.String(1000),nullable=False),sa.Column("recorded_at",sa.String(32),nullable=False),
  sa.Column("payload_json",sa.String(),nullable=False),sa.Column("posted",sa.Boolean(create_constraint=True,name="posted"),nullable=False,server_default="0"),
  sa.UniqueConstraint("request_key"),
  sa.CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128 AND length(trim(reason)) BETWEEN 1 AND 1000",name="audit"),
  sa.CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991",name="amount"),
  sa.CheckConstraint("COALESCE((kind='income_receipt' AND payer_code IS NULL AND account_id IS NOT NULL) OR (kind='repayment' AND payer_code='parents' AND account_id IS NOT NULL) OR (kind IN ('invoice_payment','expense_payment') AND ((payer_code='self' AND account_id IS NOT NULL) OR (payer_code='parents' AND account_id IS NULL))),0)",name="nature"))
 op.create_table("history_allocations",sa.Column("id",sa.Integer(),primary_key=True),
  sa.Column("event_id",sa.Integer(),sa.ForeignKey("history_events.id",ondelete="RESTRICT"),nullable=False),
  sa.Column("record_id",sa.Integer(),sa.ForeignKey("history_records.id",ondelete="RESTRICT"),nullable=False),
  sa.Column("amount_cents",sa.Integer(),nullable=False),sa.UniqueConstraint("event_id","record_id"),
  sa.CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991",name="amount"))
 op.create_table("history_event_reversals",sa.Column("id",sa.Integer(),primary_key=True),
  sa.Column("request_key",sa.String(128),nullable=False),sa.Column("event_id",sa.Integer(),sa.ForeignKey("history_events.id",ondelete="RESTRICT"),nullable=False),
  sa.Column("reason",sa.String(1000),nullable=False),sa.Column("recorded_at",sa.String(32),nullable=False),sa.Column("payload_json",sa.String(),nullable=False),
  sa.UniqueConstraint("request_key"),sa.UniqueConstraint("event_id"),
  sa.CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128 AND length(trim(reason)) BETWEEN 1 AND 1000",name="audit"))
 for sql in TRIGGERS.values():op.execute(sql)

def downgrade():
 if op.get_bind().exec_driver_sql("SELECT COUNT(*) FROM history_events").scalar():raise RuntimeError("Preservar fatos históricos; restaurar outro arquivo.")
 for name in reversed(TRIGGERS):op.execute(f"DROP TRIGGER {name}")
 for table in ("history_event_reversals","history_allocations","history_events"):op.drop_table(table)
