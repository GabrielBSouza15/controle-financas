"""Ajustes aditivos, devoluções reais, correções de vínculos e transferências.
Revision ID: 0005_adjustments
"""
from alembic import op
import sqlalchemy as sa
import importlib

revision="0005_adjustments"
down_revision="0004_settlements"
branch_labels=None
depends_on=None

def audit():
    return [sa.Column("id",sa.Integer(),primary_key=True),sa.Column("request_key",sa.String(128),nullable=False),
        sa.Column("reason",sa.String(1000),nullable=False),sa.Column("recorded_at",sa.String(32),nullable=False),
        sa.Column("payload_json",sa.String(),nullable=False),sa.UniqueConstraint("request_key"),
        sa.CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128",name="request_key"),
        sa.CheckConstraint("length(trim(reason)) BETWEEN 1 AND 1000",name="reason")]

def money(column="amount_cents"):
    return sa.CheckConstraint(f"typeof({column})='integer' AND {column} BETWEEN 1 AND 9007199254740991",name="amount")

TABLES=["obligation_adjustments","settlement_corrections","allocation_offsets","cash_transfers","transfer_reversals","invoice_reconciliations","obligation_moves"]
REPLACED=["batch_insert","batch_seal","allocation_capacity_payment","allocation_capacity_parents",
    "batch_capacity_payment","batch_capacity_parents","closure_validate"]
ADJUSTED="o.amount_cents+COALESCE((SELECT SUM(z.delta_cents) FROM obligation_adjustments z WHERE z.obligation_id=o.id),0)"
OFFSET="COALESCE((SELECT SUM(x.amount_cents) FROM allocation_offsets x JOIN settlement_corrections c ON c.id=x.correction_id WHERE x.allocation_id=a.id AND c.posted=1),0)"
ACTIVE="NOT EXISTS(SELECT 1 FROM event_reversals r WHERE r.event_id=f.id)"
EVENT_USED="""COALESCE((SELECT SUM(amount_cents) FROM allocation_batches WHERE event_id=NEW.event_id AND posted=1),0)
-COALESCE((SELECT SUM(amount_cents) FROM settlement_corrections WHERE event_id=NEW.event_id AND action='release' AND posted=1),0)
+COALESCE((SELECT SUM(unallocated_cents) FROM settlement_corrections WHERE event_id=NEW.event_id AND action='refund' AND posted=1),0)"""
TRIGGERS={
"batch_insert":"""CREATE TRIGGER batch_insert BEFORE INSERT ON allocation_batches BEGIN
 SELECT CASE WHEN NEW.posted!=0 THEN RAISE(ABORT,'batch must start unposted') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM event_reversals WHERE event_id=NEW.event_id) THEN RAISE(ABORT,'reversed event') END;
 SELECT CASE WHEN NEW.request_key!=(SELECT request_key FROM financial_events WHERE id=NEW.event_id) AND NOT EXISTS(SELECT 1 FROM financial_events f JOIN allocation_batches b ON b.event_id=f.id AND b.request_key=f.request_key AND b.posted=1 WHERE f.id=NEW.event_id) THEN RAISE(ABORT,'initial batch must be confirmed') END;
END""",
"batch_seal":f"""CREATE TRIGGER batch_seal BEFORE UPDATE ON allocation_batches BEGIN
 SELECT CASE WHEN OLD.posted!=0 OR NEW.posted!=1 OR NEW.id IS NOT OLD.id OR NEW.request_key IS NOT OLD.request_key OR NEW.event_id IS NOT OLD.event_id OR NEW.amount_cents IS NOT OLD.amount_cents OR NEW.reason IS NOT OLD.reason OR NEW.recorded_at IS NOT OLD.recorded_at OR NEW.payload_json IS NOT OLD.payload_json THEN RAISE(ABORT,'batch only allows confirmation') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM event_reversals WHERE event_id=NEW.event_id) THEN RAISE(ABORT,'reversed event') END;
 SELECT CASE WHEN COALESCE((SELECT SUM(amount_cents) FROM settlement_allocations WHERE batch_id=NEW.id),0)!=NEW.amount_cents THEN RAISE(ABORT,'incomplete allocation batch') END;
 SELECT CASE WHEN NEW.amount_cents>(SELECT amount_cents FROM financial_events WHERE id=NEW.event_id)-({EVENT_USED}) THEN RAISE(ABORT,'allocations exceed available funds') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM financial_events WHERE id=NEW.event_id AND request_key=NEW.request_key AND kind!='repayment' AND amount_cents!=NEW.amount_cents) THEN RAISE(ABORT,'initial payment must be fully allocated') END;
END""",
"adjustment_validate":f"""CREATE TRIGGER adjustment_validate BEFORE INSERT ON obligation_adjustments BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM obligations o JOIN entries e ON e.id=o.entry_id WHERE o.id=NEW.obligation_id AND e.kind='expense' AND NOT EXISTS(SELECT 1 FROM entry_changes WHERE source_entry_id=e.id) AND NEW.occurred_on>=e.occurred_on) OR NEW.occurred_on>date('now','-3 hours') THEN RAISE(ABORT,'invalid adjustment origin or date') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM obligations o WHERE o.id=NEW.obligation_id AND ({ADJUSTED})+NEW.delta_cents BETWEEN 0 AND 9007199254740991) THEN RAISE(ABORT,'invalid adjusted amount') END;
END""",
"correction_insert":"""CREATE TRIGGER correction_insert BEFORE INSERT ON settlement_corrections BEGIN
 SELECT CASE WHEN NEW.posted!=0 OR NOT EXISTS(SELECT 1 FROM financial_events f JOIN allocation_batches b ON b.event_id=f.id AND b.request_key=f.request_key AND b.posted=1 WHERE f.id=NEW.event_id AND NOT EXISTS(SELECT 1 FROM event_reversals WHERE event_id=f.id)) THEN RAISE(ABORT,'correction needs active confirmed event') END;
 SELECT CASE WHEN NEW.occurred_on>date('now','-3 hours') OR (NEW.action='refund' AND NEW.occurred_on<(SELECT occurred_on FROM financial_events WHERE id=NEW.event_id)) THEN RAISE(ABORT,'invalid correction date') END;
 SELECT CASE WHEN NEW.unallocated_cents>0 AND NOT EXISTS(SELECT 1 FROM financial_events WHERE id=NEW.event_id AND kind='repayment') THEN RAISE(ABORT,'only repayment has unallocated funds') END;
 SELECT CASE WHEN NEW.action='refund' AND EXISTS(SELECT 1 FROM financial_events f LEFT JOIN responsibles p ON p.id=f.payer_id WHERE f.id=NEW.event_id AND (f.kind IN ('repayment','income_receipt') OR p.code='self')) AND NOT EXISTS(SELECT 1 FROM accounts a JOIN responsibles p ON p.id=a.owner_id WHERE a.id=NEW.account_id AND p.code='self') THEN RAISE(ABORT,'own refund requires own account') END;
 SELECT CASE WHEN NEW.action='refund' AND EXISTS(SELECT 1 FROM financial_events f JOIN responsibles p ON p.id=f.payer_id WHERE f.id=NEW.event_id AND f.kind IN ('invoice_payment','expense_payment') AND p.code='parents') AND NEW.account_id IS NOT NULL THEN RAISE(ABORT,'external refund has no own cash') END;
END""",
"offset_insert":"""CREATE TRIGGER offset_insert BEFORE INSERT ON allocation_offsets BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM settlement_corrections c JOIN settlement_allocations a ON a.id=NEW.allocation_id JOIN allocation_batches b ON b.id=a.batch_id WHERE c.id=NEW.correction_id AND c.posted=0 AND c.event_id=b.event_id AND b.posted=1) THEN RAISE(ABORT,'offset origin mismatch') END;
 SELECT CASE WHEN NEW.amount_cents>(SELECT amount_cents FROM settlement_allocations WHERE id=NEW.allocation_id)-COALESCE((SELECT SUM(x.amount_cents) FROM allocation_offsets x JOIN settlement_corrections c ON c.id=x.correction_id WHERE x.allocation_id=NEW.allocation_id AND (c.posted=1 OR c.id=NEW.correction_id)),0) THEN RAISE(ABORT,'offset exceeds allocation') END;
END""",
"correction_seal":f"""CREATE TRIGGER correction_seal BEFORE UPDATE ON settlement_corrections BEGIN
 SELECT CASE WHEN OLD.posted!=0 OR NEW.posted!=1 OR NEW.id IS NOT OLD.id OR NEW.request_key IS NOT OLD.request_key OR NEW.event_id IS NOT OLD.event_id OR NEW.action IS NOT OLD.action OR NEW.occurred_on IS NOT OLD.occurred_on OR NEW.amount_cents IS NOT OLD.amount_cents OR NEW.unallocated_cents IS NOT OLD.unallocated_cents OR NEW.account_id IS NOT OLD.account_id OR NEW.reason IS NOT OLD.reason OR NEW.recorded_at IS NOT OLD.recorded_at OR NEW.payload_json IS NOT OLD.payload_json THEN RAISE(ABORT,'correction only allows confirmation') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM event_reversals WHERE event_id=NEW.event_id) THEN RAISE(ABORT,'reversed event') END;
 SELECT CASE WHEN COALESCE((SELECT SUM(amount_cents) FROM allocation_offsets WHERE correction_id=NEW.id),0)+NEW.unallocated_cents!=NEW.amount_cents THEN RAISE(ABORT,'incomplete correction') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM allocation_offsets x JOIN settlement_allocations a ON a.id=x.allocation_id WHERE x.correction_id=NEW.id AND a.amount_cents<(SELECT SUM(y.amount_cents) FROM allocation_offsets y JOIN settlement_corrections c ON c.id=y.correction_id WHERE y.allocation_id=a.id AND (c.posted=1 OR c.id=NEW.id))) THEN RAISE(ABORT,'offset capacity changed') END;
 SELECT CASE WHEN NEW.action='refund' AND NEW.amount_cents>(SELECT amount_cents FROM financial_events WHERE id=NEW.event_id)-COALESCE((SELECT SUM(amount_cents) FROM settlement_corrections WHERE event_id=NEW.event_id AND action='refund' AND posted=1),0) THEN RAISE(ABORT,'refund exceeds event') END;
 SELECT CASE WHEN NEW.unallocated_cents>(SELECT amount_cents FROM financial_events WHERE id=NEW.event_id)-({EVENT_USED}) THEN RAISE(ABORT,'unallocated refund exceeds available funds') END;
END""",
"refund_blocks_reversal":"""CREATE TRIGGER refund_blocks_reversal BEFORE INSERT ON event_reversals WHEN EXISTS(SELECT 1 FROM settlement_corrections WHERE event_id=NEW.event_id AND action='refund' AND posted=1) BEGIN SELECT RAISE(ABORT,'real refund protects original event'); END""",
"transfer_validate":"""CREATE TRIGGER transfer_validate BEFORE INSERT ON cash_transfers BEGIN
 SELECT CASE WHEN NEW.occurred_on>date('now','-3 hours') OR (SELECT COUNT(*) FROM accounts a JOIN responsibles r ON r.id=a.owner_id WHERE a.id IN (NEW.source_account_id,NEW.target_account_id) AND r.code='self')!=2 THEN RAISE(ABORT,'transfer requires two own accounts and effective date') END;
END""",
"adjusted_entry_change":"""CREATE TRIGGER adjusted_entry_change BEFORE INSERT ON entry_changes WHEN EXISTS(SELECT 1 FROM obligation_adjustments a JOIN obligations o ON o.id=a.obligation_id WHERE o.entry_id=NEW.source_entry_id) BEGIN SELECT RAISE(ABORT,'adjusted entry is protected'); END""",
"adjusted_account_owner":"""CREATE TRIGGER adjusted_account_owner BEFORE UPDATE OF owner_id ON accounts WHEN NEW.owner_id!=OLD.owner_id AND (EXISTS(SELECT 1 FROM cash_transfers WHERE source_account_id=OLD.id OR target_account_id=OLD.id) OR EXISTS(SELECT 1 FROM settlement_corrections WHERE account_id=OLD.id)) BEGIN SELECT RAISE(ABORT,'used account ownership is immutable'); END""",
"closure_validate":f"""CREATE TRIGGER closure_validate BEFORE INSERT ON invoice_closures BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM invoices WHERE id=NEW.invoice_id AND status='open') THEN RAISE(ABORT,'invoice already closed or missing') END;
 SELECT CASE WHEN NEW.total_cents!=COALESCE((SELECT SUM({ADJUSTED}) FROM obligations o JOIN entries e ON e.id=o.entry_id WHERE o.invoice_id=NEW.invoice_id AND NOT EXISTS(SELECT 1 FROM entry_changes WHERE source_entry_id=e.id)),0) THEN RAISE(ABORT,'adjusted invoice total changed') END;
END""",
}
for dim,expr in [("payment","f.kind!='repayment'"),("parents","f.kind='repayment' OR payer.code='parents'")]:
    used=f"""COALESCE((SELECT SUM(a.amount_cents-({OFFSET})) FROM settlement_allocations a JOIN allocation_batches b ON b.id=a.batch_id JOIN financial_events f ON f.id=b.event_id LEFT JOIN responsibles payer ON payer.id=f.payer_id WHERE a.obligation_id=o.id AND (b.posted=1 OR b.id=NEW.batch_id) AND ({expr}) AND {ACTIVE}),0)"""
    TRIGGERS[f"allocation_capacity_{dim}"]=f"""CREATE TRIGGER allocation_capacity_{dim} BEFORE INSERT ON settlement_allocations WHEN EXISTS(SELECT 1 FROM financial_events f JOIN allocation_batches b ON b.event_id=f.id LEFT JOIN responsibles payer ON payer.id=f.payer_id WHERE b.id=NEW.batch_id AND ({expr})) BEGIN SELECT CASE WHEN NEW.amount_cents>(SELECT ({ADJUSTED})-({used}) FROM obligations o WHERE o.id=NEW.obligation_id) THEN RAISE(ABORT,'adjusted capacity exceeded') END; END"""
    used=used.replace("NEW.batch_id","NEW.id")
    TRIGGERS[f"batch_capacity_{dim}"]=f"""CREATE TRIGGER batch_capacity_{dim} BEFORE UPDATE ON allocation_batches WHEN NEW.posted=1 AND EXISTS(SELECT 1 FROM financial_events f LEFT JOIN responsibles payer ON payer.id=f.payer_id WHERE f.id=NEW.event_id AND ({expr})) BEGIN SELECT CASE WHEN EXISTS(SELECT 1 FROM settlement_allocations current JOIN obligations o ON o.id=current.obligation_id WHERE current.batch_id=NEW.id AND ({ADJUSTED})<({used})) THEN RAISE(ABORT,'adjusted capacity changed') END; END"""
TRIGGERS["cycle_move_validate"]="""CREATE TRIGGER cycle_move_validate BEFORE INSERT ON obligation_moves BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM obligations o JOIN entries e ON e.id=o.entry_id JOIN invoices src ON src.id=o.invoice_id JOIN invoices dst ON dst.id=NEW.target_invoice_id WHERE o.id=NEW.obligation_id AND o.invoice_id=NEW.source_invoice_id AND o.scheduled_date=NEW.source_scheduled_on AND dst.due_date=NEW.target_scheduled_on AND src.status='open' AND dst.status='open' AND src.id!=dst.id AND src.card_id=dst.card_id AND dst.closing_date>=e.occurred_on AND NOT EXISTS(SELECT 1 FROM entry_changes WHERE source_entry_id=e.id) AND NOT EXISTS(SELECT 1 FROM obligations other JOIN settlement_allocations a ON a.obligation_id=other.id WHERE other.entry_id=e.id) AND NOT EXISTS(SELECT 1 FROM obligations other JOIN obligation_adjustments a ON a.obligation_id=other.id WHERE other.entry_id=e.id)) THEN RAISE(ABORT,'cycle move needs open unsettled origin and target') END;
END"""
TRIGGERS["cycle_move_apply"]="""CREATE TRIGGER cycle_move_apply AFTER INSERT ON obligation_moves BEGIN UPDATE obligations SET invoice_id=NEW.target_invoice_id,scheduled_date=NEW.target_scheduled_on WHERE id=NEW.obligation_id; END"""
TRIGGERS["moved_obligation_validate"]="""CREATE TRIGGER moved_obligation_validate BEFORE UPDATE ON obligations WHEN EXISTS(SELECT 1 FROM obligation_moves WHERE obligation_id=OLD.id) AND (NEW.id IS NOT OLD.id OR NEW.entry_id IS NOT OLD.entry_id OR NEW.number IS NOT OLD.number OR NEW.amount_cents IS NOT OLD.amount_cents OR NEW.invoice_id IS NOT (SELECT target_invoice_id FROM obligation_moves WHERE obligation_id=OLD.id ORDER BY id DESC LIMIT 1) OR NEW.scheduled_date IS NOT (SELECT target_scheduled_on FROM obligation_moves WHERE obligation_id=OLD.id ORDER BY id DESC LIMIT 1)) BEGIN SELECT RAISE(ABORT,'cycle history requires explicit move'); END"""
TRIGGERS["moved_obligation_no_delete"]="""CREATE TRIGGER moved_obligation_no_delete BEFORE DELETE ON obligations WHEN EXISTS(SELECT 1 FROM obligation_moves WHERE obligation_id=OLD.id) BEGIN SELECT RAISE(ABORT,'cycle history immutable'); END"""
for table in TABLES:
    for action in ("UPDATE","DELETE"):
        if table=="settlement_corrections" and action=="UPDATE": continue
        name=f"{table}_no_{action.lower()}"
        TRIGGERS[name]=f"CREATE TRIGGER {name} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'adjustment history is immutable'); END"
for table,column in [("entries","id"),("obligations","entry_id")]:
    for action in ("UPDATE","DELETE"):
        name=f"adjusted_{table}_{action.lower()}"
        TRIGGERS[name]=f"CREATE TRIGGER {name} BEFORE {action} ON {table} WHEN EXISTS(SELECT 1 FROM obligation_adjustments a JOIN obligations o ON o.id=a.obligation_id WHERE o.entry_id=OLD.{column}) BEGIN SELECT RAISE(ABORT,'adjusted history is immutable'); END"
TRIGGERS["adjusted_obligation_insert"]="CREATE TRIGGER adjusted_obligation_insert BEFORE INSERT ON obligations WHEN EXISTS(SELECT 1 FROM obligation_adjustments a JOIN obligations o ON o.id=a.obligation_id WHERE o.entry_id=NEW.entry_id) BEGIN SELECT RAISE(ABORT,'adjusted schedule is immutable'); END"

def upgrade():
    op.create_table("obligation_adjustments",*audit(),sa.Column("obligation_id",sa.Integer(),sa.ForeignKey("obligations.id",ondelete="RESTRICT"),nullable=False),sa.Column("delta_cents",sa.Integer(),nullable=False),sa.Column("occurred_on",sa.Date(),nullable=False),sa.CheckConstraint("typeof(delta_cents)='integer' AND delta_cents != 0 AND abs(delta_cents)<=9007199254740991",name="delta"))
    op.create_table("settlement_corrections",*audit(),sa.Column("event_id",sa.Integer(),sa.ForeignKey("financial_events.id",ondelete="RESTRICT"),nullable=False),sa.Column("action",sa.String(16),nullable=False),sa.Column("occurred_on",sa.Date(),nullable=False),sa.Column("amount_cents",sa.Integer(),nullable=False),sa.Column("unallocated_cents",sa.Integer(),nullable=False,server_default="0"),sa.Column("account_id",sa.Integer(),sa.ForeignKey("accounts.id",ondelete="RESTRICT")),sa.Column("posted",sa.Boolean(create_constraint=True,name="posted"),nullable=False,server_default="0"),money(),sa.CheckConstraint("action IN ('release','refund')",name="action"),sa.CheckConstraint("typeof(unallocated_cents)='integer' AND unallocated_cents BETWEEN 0 AND amount_cents",name="unallocated"),sa.CheckConstraint("action='refund' OR (account_id IS NULL AND unallocated_cents=0)",name="release"))
    op.create_table("allocation_offsets",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("correction_id",sa.Integer(),sa.ForeignKey("settlement_corrections.id",ondelete="RESTRICT"),nullable=False),sa.Column("allocation_id",sa.Integer(),sa.ForeignKey("settlement_allocations.id",ondelete="RESTRICT"),nullable=False),sa.Column("amount_cents",sa.Integer(),nullable=False),sa.UniqueConstraint("correction_id","allocation_id"),money())
    op.create_table("cash_transfers",*audit(),sa.Column("source_account_id",sa.Integer(),sa.ForeignKey("accounts.id",ondelete="RESTRICT"),nullable=False),sa.Column("target_account_id",sa.Integer(),sa.ForeignKey("accounts.id",ondelete="RESTRICT"),nullable=False),sa.Column("occurred_on",sa.Date(),nullable=False),sa.Column("amount_cents",sa.Integer(),nullable=False),money(),sa.CheckConstraint("source_account_id!=target_account_id",name="accounts"))
    op.create_table("transfer_reversals",*audit(),sa.Column("transfer_id",sa.Integer(),sa.ForeignKey("cash_transfers.id",ondelete="RESTRICT"),nullable=False),sa.UniqueConstraint("transfer_id"))
    op.create_table("invoice_reconciliations",*audit(),sa.Column("invoice_id",sa.Integer(),sa.ForeignKey("invoices.id",ondelete="RESTRICT"),nullable=False),sa.Column("expected_total_cents",sa.Integer(),nullable=False),sa.Column("calculated_total_cents",sa.Integer(),nullable=False),sa.Column("snapshot_json",sa.String(),nullable=False),sa.CheckConstraint("typeof(expected_total_cents)='integer' AND abs(expected_total_cents)<=9007199254740991",name="expected"),sa.CheckConstraint("typeof(calculated_total_cents)='integer' AND abs(calculated_total_cents)<=9007199254740991",name="calculated"))
    op.create_table("obligation_moves",*audit(),sa.Column("obligation_id",sa.Integer(),sa.ForeignKey("obligations.id",ondelete="RESTRICT"),nullable=False),sa.Column("source_invoice_id",sa.Integer(),sa.ForeignKey("invoices.id",ondelete="RESTRICT"),nullable=False),sa.Column("target_invoice_id",sa.Integer(),sa.ForeignKey("invoices.id",ondelete="RESTRICT"),nullable=False),sa.Column("source_scheduled_on",sa.Date(),nullable=False),sa.Column("target_scheduled_on",sa.Date(),nullable=False))
    for name in REPLACED: op.execute(f"DROP TRIGGER {name}")
    for sql in TRIGGERS.values(): op.execute(sql)

def downgrade():
    if any(op.get_bind().exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar() for table in TABLES):
        raise RuntimeError("Reversão bloqueada: preservar histórico financeiro e ajustes.")
    for name in reversed(TRIGGERS): op.execute(f"DROP TRIGGER {name}")
    for table in reversed(TABLES): op.drop_table(table)
    previous=importlib.import_module("migrations.versions.0004_settlements")
    for name in REPLACED: op.execute(previous.TRIGGERS[name])
