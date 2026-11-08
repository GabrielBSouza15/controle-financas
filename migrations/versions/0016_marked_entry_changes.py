"""Revisar indicação sem conta por versões, preservando a evidência original."""
from alembic import op

revision = "0016_marked_entry_changes"
down_revision = "0015_entry_payment_marks"
branch_labels = None
depends_on = None


def upgrade():
    # Somente o bloqueio da marca é retirado. Vínculos reais, ajustes,
    # consolidação e imutabilidade continuam protegidos pelos outros gatilhos.
    op.execute("DROP TRIGGER entry_payment_mark_change")


def downgrade():
    if op.get_bind().exec_driver_sql("""SELECT count(*) FROM entry_changes c
        JOIN entry_payment_marks m ON m.entry_id=c.source_entry_id""").scalar():
        raise RuntimeError("Preservar correções de indicação sem conta; restaurar para arquivo novo.")
    op.execute("""CREATE TRIGGER entry_payment_mark_change BEFORE INSERT ON entry_changes
    WHEN EXISTS (SELECT 1 FROM entry_payment_marks WHERE entry_id=NEW.source_entry_id)
    BEGIN SELECT RAISE(ABORT,'paid entry must preserve payment evidence'); END""")
