"""Liquidações imutáveis, alocações explícitas, anulação e fechamento auditado.

Revision ID: 0004_settlements
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_settlements"
down_revision = "0003_entry_changes"
branch_labels = None
depends_on = None


def audit_columns():
    return [sa.Column("reason",sa.String(1000),nullable=False),sa.Column("recorded_at",sa.String(32),nullable=False),sa.Column("payload_json",sa.String(),nullable=False)]


def audit_checks():
    return [sa.CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128",name="request_key"),sa.CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000",name="reason")]

# As expressões abaixo são congeladas nesta revisão; não importam modelos vivos.
ACTIVE_EVENT = "NOT EXISTS(SELECT 1 FROM event_reversals r WHERE r.event_id=f.id)"
TRIGGERS = {
"financial_event_validate": """CREATE TRIGGER financial_event_validate BEFORE INSERT ON financial_events BEGIN
 SELECT CASE WHEN NEW.kind='repayment' AND NOT EXISTS(SELECT 1 FROM responsibles WHERE id=NEW.payer_id AND code='parents') THEN RAISE(ABORT,'repayment must come from parents') END;
 SELECT CASE WHEN (NEW.kind IN ('income_receipt','repayment') OR EXISTS(SELECT 1 FROM responsibles WHERE id=NEW.payer_id AND code='self')) AND NOT EXISTS(SELECT 1 FROM accounts a JOIN responsibles r ON r.id=a.owner_id WHERE a.id=NEW.account_id AND r.code='self') THEN RAISE(ABORT,'own cash requires own account') END;
 SELECT CASE WHEN NEW.kind IN ('invoice_payment','expense_payment') AND EXISTS(SELECT 1 FROM responsibles WHERE id=NEW.payer_id AND code='parents') AND NEW.account_id IS NOT NULL THEN RAISE(ABORT,'external payment cannot move own account') END;
END""",
"batch_insert": """CREATE TRIGGER batch_insert BEFORE INSERT ON allocation_batches BEGIN
 SELECT CASE WHEN NEW.posted!=0 THEN RAISE(ABORT,'batch must start unposted') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM event_reversals WHERE event_id=NEW.event_id) THEN RAISE(ABORT,'reversed event cannot receive allocations') END;
 SELECT CASE WHEN NEW.request_key!=(SELECT request_key FROM financial_events WHERE id=NEW.event_id) AND NOT EXISTS(SELECT 1 FROM financial_events f JOIN allocation_batches b ON b.event_id=f.id AND b.request_key=f.request_key AND b.posted=1 WHERE f.id=NEW.event_id AND f.kind='repayment') THEN RAISE(ABORT,'only confirmed repayment can receive later allocation') END;
END""",
"batch_seal": """CREATE TRIGGER batch_seal BEFORE UPDATE ON allocation_batches BEGIN
 SELECT CASE WHEN OLD.posted!=0 OR NEW.posted!=1 OR NEW.id IS NOT OLD.id OR NEW.request_key IS NOT OLD.request_key OR NEW.event_id IS NOT OLD.event_id OR NEW.amount_cents IS NOT OLD.amount_cents OR NEW.reason IS NOT OLD.reason OR NEW.recorded_at IS NOT OLD.recorded_at OR NEW.payload_json IS NOT OLD.payload_json THEN RAISE(ABORT,'batch only allows confirmation') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM event_reversals WHERE event_id=NEW.event_id) THEN RAISE(ABORT,'reversed event') END;
 SELECT CASE WHEN COALESCE((SELECT SUM(amount_cents) FROM settlement_allocations WHERE batch_id=NEW.id),0)!=NEW.amount_cents THEN RAISE(ABORT,'incomplete allocation batch') END;
 SELECT CASE WHEN NEW.amount_cents>(SELECT amount_cents FROM financial_events WHERE id=NEW.event_id)-COALESCE((SELECT SUM(amount_cents) FROM allocation_batches WHERE event_id=NEW.event_id AND posted=1),0) THEN RAISE(ABORT,'allocations exceed event') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM financial_events WHERE id=NEW.event_id AND kind!='repayment' AND amount_cents!=NEW.amount_cents) THEN RAISE(ABORT,'payment must be fully allocated') END;
END""",
"allocation_insert": """CREATE TRIGGER allocation_insert BEFORE INSERT ON settlement_allocations BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM allocation_batches WHERE id=NEW.batch_id AND posted=0) THEN RAISE(ABORT,'confirmed batch immutable') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM obligations o JOIN entries e ON e.id=o.entry_id WHERE o.id=NEW.obligation_id AND NOT EXISTS(SELECT 1 FROM entry_changes c WHERE c.source_entry_id=e.id)) THEN RAISE(ABORT,'inactive obligation') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM financial_events f JOIN allocation_batches b ON b.event_id=f.id JOIN event_reversals r ON r.event_id=f.id WHERE b.id=NEW.batch_id) THEN RAISE(ABORT,'reversed event') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM financial_events f JOIN allocation_batches b ON b.event_id=f.id JOIN obligations o ON o.id=NEW.obligation_id JOIN entries e ON e.id=o.entry_id JOIN responsibles person ON person.id=e.responsible_id LEFT JOIN responsibles payer ON payer.id=f.payer_id WHERE b.id=NEW.batch_id AND
 ((f.kind='invoice_payment' AND e.kind='expense' AND o.invoice_id=f.invoice_id) OR (f.kind='repayment' AND e.kind='expense' AND person.code='parents') OR (f.kind='expense_payment' AND e.kind='expense' AND o.invoice_id IS NULL) OR (f.kind='income_receipt' AND e.kind='income' AND o.invoice_id IS NULL)) AND (f.kind='repayment' OR f.occurred_on>=e.occurred_on) AND (payer.code IS NULL OR payer.code!='parents' OR person.code='parents')) THEN RAISE(ABORT,'allocation does not match financial event') END;
 SELECT CASE WHEN NEW.amount_cents>(SELECT amount_cents FROM allocation_batches WHERE id=NEW.batch_id)-COALESCE((SELECT SUM(amount_cents) FROM settlement_allocations WHERE batch_id=NEW.batch_id),0) THEN RAISE(ABORT,'batch capacity exceeded') END;
END""",
"reversal_validate": """CREATE TRIGGER reversal_validate BEFORE INSERT ON event_reversals WHEN NOT EXISTS(SELECT 1 FROM financial_events f JOIN allocation_batches b ON b.event_id=f.id AND b.request_key=f.request_key WHERE f.id=NEW.event_id AND b.posted=1) BEGIN SELECT RAISE(ABORT,'only confirmed events can be reversed'); END""",
"settled_entry_change": """CREATE TRIGGER settled_entry_change BEFORE INSERT ON entry_changes WHEN EXISTS(SELECT 1 FROM settlement_allocations a JOIN obligations o ON o.id=a.obligation_id WHERE o.entry_id=NEW.source_entry_id) BEGIN SELECT RAISE(ABORT,'settled entry requires explicit adjustment'); END""",
"closure_validate": """CREATE TRIGGER closure_validate BEFORE INSERT ON invoice_closures BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM invoices WHERE id=NEW.invoice_id AND status='open') THEN RAISE(ABORT,'invoice already closed or missing') END;
 SELECT CASE WHEN NEW.total_cents!=COALESCE((SELECT SUM(o.amount_cents) FROM obligations o JOIN entries e ON e.id=o.entry_id WHERE o.invoice_id=NEW.invoice_id AND NOT EXISTS(SELECT 1 FROM entry_changes WHERE source_entry_id=e.id)),0) THEN RAISE(ABORT,'invoice total changed') END;
END""",
"closure_apply": """CREATE TRIGGER closure_apply AFTER INSERT ON invoice_closures BEGIN UPDATE invoices SET status='closed' WHERE id=NEW.invoice_id; END""",
}
# Dimensões independentes: emissor/obrigação e cobertura da responsabilidade do grupo.
for dimension, applies, used in [
 ("payment", "f.kind IN ('invoice_payment','expense_payment','income_receipt')", "f.kind IN ('invoice_payment','expense_payment','income_receipt')"),
 ("parents", "f.kind='repayment' OR payer.code='parents'", "f.kind='repayment' OR payer.code='parents'"),
]:
    TRIGGERS[f"allocation_capacity_{dimension}"] = f"""CREATE TRIGGER allocation_capacity_{dimension} BEFORE INSERT ON settlement_allocations
 WHEN EXISTS(SELECT 1 FROM financial_events f JOIN allocation_batches b ON b.event_id=f.id LEFT JOIN responsibles payer ON payer.id=f.payer_id WHERE b.id=NEW.batch_id AND ({applies}))
 BEGIN SELECT CASE WHEN NEW.amount_cents>(SELECT amount_cents FROM obligations WHERE id=NEW.obligation_id)-COALESCE((SELECT SUM(a.amount_cents) FROM settlement_allocations a JOIN allocation_batches b ON b.id=a.batch_id JOIN financial_events f ON f.id=b.event_id LEFT JOIN responsibles payer ON payer.id=f.payer_id WHERE a.obligation_id=NEW.obligation_id AND (b.posted=1 OR b.id=NEW.batch_id) AND ({used}) AND {ACTIVE_EVENT}),0) THEN RAISE(ABORT,'obligation capacity exceeded') END; END"""
for dimension, applies, used in [
 ("payment", "f.kind!='repayment'", "f.kind!='repayment'"),
 ("parents", "f.kind='repayment' OR payer.code='parents'", "f.kind='repayment' OR payer.code='parents'"),
]:
    TRIGGERS[f"batch_capacity_{dimension}"] = f"""CREATE TRIGGER batch_capacity_{dimension} BEFORE UPDATE ON allocation_batches WHEN NEW.posted=1
 AND EXISTS(SELECT 1 FROM financial_events f LEFT JOIN responsibles payer ON payer.id=f.payer_id WHERE f.id=NEW.event_id AND ({applies}))
 BEGIN SELECT CASE WHEN EXISTS(SELECT 1 FROM settlement_allocations current JOIN obligations o ON o.id=current.obligation_id WHERE current.batch_id=NEW.id AND o.amount_cents<(SELECT COALESCE(SUM(a.amount_cents),0) FROM settlement_allocations a JOIN allocation_batches b ON b.id=a.batch_id JOIN financial_events f ON f.id=b.event_id LEFT JOIN responsibles payer ON payer.id=f.payer_id WHERE a.obligation_id=o.id AND (b.posted=1 OR b.id=NEW.id) AND ({used}) AND {ACTIVE_EVENT})) THEN RAISE(ABORT,'capacity changed before confirmation') END; END"""
for table in ["financial_events","settlement_allocations","event_reversals","invoice_closures"]:
    for action in ["UPDATE","DELETE"]:
        name=f"{table}_no_{action.lower()}"
        TRIGGERS[name]=f"CREATE TRIGGER {name} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'financial history is immutable'); END"
TRIGGERS["batch_no_delete"]="CREATE TRIGGER batch_no_delete BEFORE DELETE ON allocation_batches BEGIN SELECT RAISE(ABORT,'financial history is immutable'); END"
for table,column in [("entries","id"),("obligations","entry_id")]:
    for action in ["UPDATE","DELETE"]:
        name=f"settled_{table}_{action.lower()}"
        TRIGGERS[name]=f"CREATE TRIGGER {name} BEFORE {action} ON {table} WHEN EXISTS(SELECT 1 FROM settlement_allocations a JOIN obligations o ON o.id=a.obligation_id WHERE o.entry_id=OLD.{column}) BEGIN SELECT RAISE(ABORT,'settled history is immutable'); END"
TRIGGERS["settled_obligation_insert"]="""CREATE TRIGGER settled_obligation_insert BEFORE INSERT ON obligations WHEN EXISTS(SELECT 1 FROM settlement_allocations a JOIN obligations o ON o.id=a.obligation_id WHERE o.entry_id=NEW.entry_id) BEGIN SELECT RAISE(ABORT,'settled schedule is immutable'); END"""

TRIGGERS["used_financial_account_owner"]="""CREATE TRIGGER used_financial_account_owner BEFORE UPDATE OF owner_id ON accounts WHEN NEW.owner_id!=OLD.owner_id AND EXISTS(SELECT 1 FROM financial_events WHERE account_id=OLD.id) BEGIN SELECT RAISE(ABORT,'financial account ownership is immutable'); END"""
TRIGGERS["used_responsible_code"]="""CREATE TRIGGER used_responsible_code BEFORE UPDATE OF code ON responsibles WHEN NEW.code!=OLD.code AND (EXISTS(SELECT 1 FROM entries WHERE responsible_id=OLD.id) OR EXISTS(SELECT 1 FROM financial_events WHERE payer_id=OLD.id) OR EXISTS(SELECT 1 FROM financial_events f JOIN accounts a ON a.id=f.account_id WHERE a.owner_id=OLD.id)) BEGIN SELECT RAISE(ABORT,'used financial role is immutable'); END"""


def upgrade():
    op.create_table("financial_events",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("request_key",sa.String(128),nullable=False),sa.Column("kind",sa.String(24),nullable=False),sa.Column("occurred_on",sa.Date(),nullable=False),sa.Column("amount_cents",sa.Integer(),nullable=False),sa.Column("payer_id",sa.Integer(),sa.ForeignKey("responsibles.id",ondelete="RESTRICT")),sa.Column("account_id",sa.Integer(),sa.ForeignKey("accounts.id",ondelete="RESTRICT")),sa.Column("invoice_id",sa.Integer(),sa.ForeignKey("invoices.id",ondelete="RESTRICT")),*audit_columns(),sa.UniqueConstraint("request_key"),*audit_checks(),sa.CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991",name="amount"),sa.CheckConstraint("kind IN ('invoice_payment','repayment','expense_payment','income_receipt')",name="kind"),sa.CheckConstraint("(kind='income_receipt' AND payer_id IS NULL) OR (kind!='income_receipt' AND payer_id IS NOT NULL)",name="payer"),sa.CheckConstraint("(kind='invoice_payment' AND invoice_id IS NOT NULL) OR (kind!='invoice_payment' AND invoice_id IS NULL)",name="invoice"))
    op.create_table("allocation_batches",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("request_key",sa.String(128),nullable=False),sa.Column("event_id",sa.Integer(),sa.ForeignKey("financial_events.id",ondelete="RESTRICT"),nullable=False),sa.Column("amount_cents",sa.Integer(),nullable=False),sa.Column("posted",sa.Boolean(create_constraint=True,name="posted"),server_default="0",nullable=False),*audit_columns(),sa.UniqueConstraint("request_key"),*audit_checks(),sa.CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 0 AND 9007199254740991",name="amount"))
    op.create_index("ix_allocation_batches_event_id","allocation_batches",["event_id"])
    op.create_table("settlement_allocations",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("batch_id",sa.Integer(),sa.ForeignKey("allocation_batches.id",ondelete="RESTRICT"),nullable=False),sa.Column("obligation_id",sa.Integer(),sa.ForeignKey("obligations.id",ondelete="RESTRICT"),nullable=False),sa.Column("amount_cents",sa.Integer(),nullable=False),sa.UniqueConstraint("batch_id","obligation_id"),sa.CheckConstraint("typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 9007199254740991",name="amount"))
    op.create_index("ix_settlement_allocations_batch_id","settlement_allocations",["batch_id"])
    op.create_index("ix_settlement_allocations_obligation_id","settlement_allocations",["obligation_id"])
    op.create_table("event_reversals",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("request_key",sa.String(128),nullable=False),sa.Column("event_id",sa.Integer(),sa.ForeignKey("financial_events.id",ondelete="RESTRICT"),nullable=False),*audit_columns(),sa.UniqueConstraint("request_key"),sa.UniqueConstraint("event_id"),*audit_checks())
    op.create_table("invoice_closures",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("request_key",sa.String(128),nullable=False),sa.Column("invoice_id",sa.Integer(),sa.ForeignKey("invoices.id",ondelete="RESTRICT"),nullable=False),sa.Column("total_cents",sa.Integer(),nullable=False),sa.Column("snapshot_json",sa.String(),nullable=False),*audit_columns(),sa.UniqueConstraint("request_key"),sa.UniqueConstraint("invoice_id"),*audit_checks(),sa.CheckConstraint("typeof(total_cents)='integer' AND total_cents BETWEEN 0 AND 9007199254740991",name="total"))
    for sql in TRIGGERS.values():
        op.execute(sql)


def downgrade():
    if any(op.get_bind().exec_driver_sql(f"SELECT COUNT(*) FROM {name}").scalar() for name in ["financial_events","invoice_closures"]):
        raise RuntimeError("Reversão bloqueada: preservar histórico financeiro.")
    for name in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER {name}")
    for name in ["invoice_closures","event_reversals","settlement_allocations","allocation_batches","financial_events"]:
        op.drop_table(name)
