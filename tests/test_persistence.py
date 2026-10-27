from app.persistence.migrations import head_revision
"""Persistência exercitada em arquivos temporários, sempre com dados fictícios."""

from datetime import date, datetime

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.persistence.database import make_engine, transaction
from app.persistence.entries import EntryDraft, IdempotencyConflict, PersistenceValidationError, record_entry
from app.persistence.migrations import current_revision, migration_config, upgrade_database
from app.persistence.models import Account, Base, Card, CardRuleVersion, Category, Entry, Invoice, Obligation, Responsible
from app.persistence.types import MAX_INTEGER


@pytest.fixture
def engine(tmp_path):
    path = tmp_path / "finance.db"
    upgrade_database(path)
    result = make_engine(path)
    yield result
    result.dispose()


@pytest.fixture
def seeded(engine):
    with transaction(engine) as session:
        session.add_all([Responsible(id=1, code="self", name="Eu"), Responsible(id=2, code="parents", name="Pai/Mãe")])
        session.flush()
        session.add_all([Account(id=1, name="Conta fictícia", owner_id=1), Category(id=1, name="Categoria fictícia"),
                         Card(id=1, name="Nubank fictício", holder_id=1), Card(id=2, name="Mercado fictício", holder_id=1)])
        session.flush()
        session.add_all([
            CardRuleVersion(id=1, card_id=1, effective_from=date(2020,1,1), closing_day=5, due_day=5, include_closing_day=False),
            CardRuleVersion(id=2, card_id=2, effective_from=date(2020,1,1), closing_day=11, due_day=11, include_closing_day=True),
        ])
    return engine


def draft(**changes):
    values = dict(request_key="request-1", description="Compra fictícia", occurred_on=date(2026,10,5),
                  total_cents=10000, installment_count=3, responsible_id=1, payment_method="credit", card_id=1, category_id=1)
    values.update(changes)
    return EntryDraft(**values)


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_schema_is_migrated_and_matches_models(engine):
    with engine.connect() as connection:
        assert set(inspect(connection).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert {"batch_seal", "closure_validate", "allocation_insert", "used_responsible_code"} <= {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'").all()}
    # Segunda conexão também deve habilitar as chaves estrangeiras.
    with engine.connect() as first, engine.connect() as second:
        assert first.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert second.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_upgrade_is_repeatable_and_downgrade_is_tested_only_in_disposable_database(tmp_path):
    path = tmp_path / "roundtrip.db"
    assert current_revision(path) is None and not path.exists()
    upgrade_database(path)
    upgrade_database(path)
    assert current_revision(path) ==head_revision()
    engine = make_engine(path)
    with engine.begin() as connection:
        config = migration_config()
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
        assert set(inspect(connection).get_table_names()) == {"alembic_version"}
    engine.dispose()
    upgrade_database(path)
    assert current_revision(path) ==head_revision()


def test_complete_card_purchase_is_persisted(seeded):
    identity = record_entry(seeded, draft())
    with Session(seeded) as session:
        entry = session.get(Entry, identity)
        assert entry.total_cents == 10000 and entry.card_rule_id == 1
        assert [part.amount_cents for part in entry.obligations] == [3334,3333,3333]
        assert [part.scheduled_date for part in entry.obligations] == [date(2026,11,5),date(2026,12,5),date(2027,1,5)]
        assert count(session, Entry) == 1 and count(session, Invoice) == 3
        assert sum(part.amount_cents for part in entry.obligations) == entry.total_cents


def test_same_operation_reuses_entry_and_different_payload_conflicts(seeded):
    identity = record_entry(seeded, draft())
    assert record_entry(seeded, draft()) == identity
    with pytest.raises(IdempotencyConflict):
        record_entry(seeded, draft(total_cents=10001))
    with Session(seeded) as session:
        assert count(session, Entry) == 1 and count(session, Obligation) == 3


def test_distinct_operation_keys_can_represent_legitimate_identical_purchases(seeded):
    first = record_entry(seeded, draft())
    second = record_entry(seeded, draft(request_key="request-2"))
    assert first != second
    with Session(seeded) as session:
        assert count(session, Invoice) == 3
        assert count(session, Obligation) == 6


@pytest.mark.parametrize("kind", ["expense", "income"])
def test_pix_monthly_obligations_do_not_generate_invoices_or_cash_events(seeded, kind):
    identity = record_entry(seeded, draft(card_id=None, payment_method="pix", kind=kind, account_id=1, occurred_on=date(2025,1,31)))
    with Session(seeded) as session:
        entry = session.get(Entry, identity)
        assert entry.card_rule_id is None
        assert [part.scheduled_date for part in entry.obligations] == [date(2025,1,31),date(2025,2,28),date(2025,3,31)]
        assert all(part.invoice_id is None for part in entry.obligations)
        assert count(session, Invoice) == 0


def test_responsibility_remains_distinct_from_invoice_total(seeded):
    record_entry(seeded, draft(total_cents=150000, installment_count=1))
    record_entry(seeded, draft(request_key="parents", total_cents=150000, installment_count=1, responsible_id=2))
    with Session(seeded) as session:
        totals = dict(session.execute(select(Entry.responsible_id, func.sum(Obligation.amount_cents)).join(Obligation).group_by(Entry.responsible_id)).all())
        assert totals == {1:150000,2:150000}
        assert session.scalar(select(func.sum(Obligation.amount_cents))) == 300000
        assert count(session, Invoice) == 1


def test_late_failure_rolls_back_entry_first_invoice_and_first_installment(seeded):
    with transaction(seeded) as session:
        session.add(Invoice(card_id=1, reference_month=date(2026,12,1), closing_date=date(2026,12,5), due_date=date(2026,12,5), status="closed"))
    with pytest.raises(PersistenceValidationError, match="consolidada"):
        record_entry(seeded, draft())
    with Session(seeded) as session:
        assert count(session, Entry) == 0 and count(session, Obligation) == 0
        assert count(session, Invoice) == 1
        assert session.scalar(select(Invoice.status)) == "closed"


def test_missing_reference_rolls_back_without_partial_invoice(seeded):
    with pytest.raises(IntegrityError):
        record_entry(seeded, draft(responsible_id=999))
    with Session(seeded) as session:
        assert count(session, Entry) == count(session, Obligation) == count(session, Invoice) == 0


def test_explicit_transaction_rolls_back_all_changes(seeded):
    with pytest.raises(RuntimeError):
        with transaction(seeded) as session:
            session.add(Category(name="Não deve ficar"))
            session.flush()
            raise RuntimeError("Falha simulada")
    with Session(seeded) as session:
        assert count(session, Category) == 1


def test_effective_rule_is_selected_and_existing_dates_are_not_rewritten(seeded):
    before = record_entry(seeded, draft(occurred_on=date(2026,9,20), installment_count=1))
    with transaction(seeded) as session:
        session.add(CardRuleVersion(id=3, card_id=1, effective_from=date(2026,11,1), closing_day=10, due_day=10, include_closing_day=True))
    after = record_entry(seeded, draft(request_key="new-rule", occurred_on=date(2026,11,9), installment_count=1))
    with Session(seeded) as session:
        assert session.get(Entry, before).card_rule_id == 1
        assert session.get(Entry, after).card_rule_id == 3
        assert session.get(Entry, before).obligations[0].scheduled_date == date(2026,10,5)
        assert session.get(Entry, after).obligations[0].scheduled_date == date(2026,11,10)
    assert record_entry(seeded, draft(occurred_on=date(2026,9,20), installment_count=1)) == before


def test_incompatible_rule_dates_for_existing_cycle_require_explicit_conciliation(seeded):
    record_entry(seeded, draft(installment_count=1))
    with transaction(seeded) as session:
        session.add(CardRuleVersion(card_id=1, effective_from=date(2026,10,10), closing_day=10, due_day=10, include_closing_day=True))
    with pytest.raises(PersistenceValidationError, match="conflitante"):
        record_entry(seeded, draft(request_key="conflict", occurred_on=date(2026,10,15), installment_count=1))
    with Session(seeded) as session:
        assert count(session, Entry) == count(session, Obligation) == count(session, Invoice) == 1


@pytest.mark.parametrize("card_id, occurred", [(999,date(2026,10,1)),(1,date(2019,1,1))])
def test_missing_card_or_rule_fails_clearly(seeded,card_id,occurred):
    with pytest.raises(PersistenceValidationError):
        record_entry(seeded,draft(card_id=card_id,occurred_on=occurred))


def test_inactive_card_is_rejected(seeded):
    with transaction(seeded) as session:
        session.get(Card,1).active=False
    with pytest.raises(PersistenceValidationError):
        record_entry(seeded,draft())


@pytest.mark.parametrize("changes", [
    {"request_key":""},{"request_key":"x"*129},{"description":" "},
    {"total_cents":1.2},{"total_cents":True},{"total_cents":0},{"total_cents":MAX_INTEGER+1},
    {"installment_count":True},{"installment_count":10001},{"responsible_id":0},
    {"occurred_on":datetime(2026,10,5)}, {"kind":"transfer"},{"payment_method":"other"},
    {"kind":"income"},{"account_id":1},{"card_id":None},{"payment_method":"pix"},
])
def test_draft_rejects_invalid_or_ambiguous_inputs(changes):
    with pytest.raises(PersistenceValidationError):
        draft(**changes)


@pytest.mark.parametrize("value", [True, 12.5, MAX_INTEGER+1])
def test_sqlalchemy_boundary_rejects_non_integer_money(seeded,value):
    identity=record_entry(seeded,draft())
    with pytest.raises(StatementError):
        with transaction(seeded) as session:
            session.get(Entry,identity).total_cents=value


def test_raw_sql_cannot_bypass_monetary_check(seeded):
    identity=record_entry(seeded,draft())
    with pytest.raises(IntegrityError):
        with seeded.begin() as connection:
            connection.exec_driver_sql("UPDATE obligations SET amount_cents = 1.5 WHERE entry_id = ?",(identity,))


def test_foreign_key_prevents_deleting_used_responsible(seeded):
    with pytest.raises(IntegrityError):
        with seeded.begin() as connection:
            connection.exec_driver_sql("DELETE FROM responsibles WHERE id=1")


def test_closed_history_is_protected_even_from_direct_sql(seeded):
    identity=record_entry(seeded,draft(installment_count=1))
    with seeded.begin() as connection:
        invoice=connection.exec_driver_sql("SELECT invoice_id FROM obligations WHERE entry_id=?",(identity,)).scalar_one()
        connection.exec_driver_sql("UPDATE invoices SET status='closed' WHERE id=?",(invoice,))
    statements=[
        ("UPDATE entries SET responsible_id=2 WHERE id=?",identity),
        ("DELETE FROM entries WHERE id=?",identity),
        ("UPDATE obligations SET amount_cents=1 WHERE entry_id=?",identity),
        ("DELETE FROM obligations WHERE entry_id=?",identity),
        ("UPDATE invoices SET status='open' WHERE id=?",invoice),
        ("DELETE FROM invoices WHERE id=?",invoice),
    ]
    for statement, key in statements:
        with pytest.raises(IntegrityError):
            with seeded.begin() as connection:
                connection.exec_driver_sql(statement,(key,))


def test_used_rule_and_invoice_dates_are_immutable(seeded):
    record_entry(seeded,draft())
    for statement in ["UPDATE card_rules SET closing_day=6 WHERE id=1", "UPDATE invoices SET due_date='2026-11-06' WHERE reference_month='2026-11-01'"]:
        with pytest.raises(IntegrityError):
            with seeded.begin() as connection:
                connection.exec_driver_sql(statement)


def test_rules_and_obligations_cannot_reference_other_cards(seeded):
    identity=record_entry(seeded,draft())
    with pytest.raises(IntegrityError):
        with seeded.begin() as connection:
            connection.exec_driver_sql("UPDATE entries SET card_rule_id=2 WHERE id=?",(identity,))
    other=record_entry(seeded,draft(request_key="other",card_id=2))
    with Session(seeded) as session:
        other_invoice=session.get(Entry,other).obligations[0].invoice_id
    with pytest.raises(IntegrityError):
        with seeded.begin() as connection:
            connection.exec_driver_sql("UPDATE obligations SET invoice_id=? WHERE entry_id=?",(other_invoice,identity))


def test_number_and_sum_limits_are_enforced(seeded):
    identity=record_entry(seeded,draft())
    for statement in ["UPDATE obligations SET number=4 WHERE entry_id=? AND number=1", "UPDATE obligations SET amount_cents=9999 WHERE entry_id=? AND number=1"]:
        with pytest.raises(IntegrityError):
            with seeded.begin() as connection:
                connection.exec_driver_sql(statement,(identity,))


def test_operation_key_and_used_card_holder_cannot_be_reassigned(seeded):
    identity=record_entry(seeded,draft())
    for statement, parameters in [
        ("UPDATE entries SET request_key='another-key' WHERE id=?",(identity,)),
        ("UPDATE cards SET holder_id=2 WHERE id=?",(1,)),
    ]:
        with pytest.raises(IntegrityError):
            with seeded.begin() as connection:
                connection.exec_driver_sql(statement,parameters)


def test_date_boundary_rejects_timestamp_in_persisted_rules(seeded):
    with pytest.raises(StatementError):
        with transaction(seeded) as session:
            session.add(CardRuleVersion(card_id=1,effective_from=datetime(2027,1,1),
                                        closing_day=5,due_day=5,include_closing_day=False))


def test_raw_database_constraints_prevent_duplicate_installment(seeded):
    identity=record_entry(seeded,draft())
    with pytest.raises(IntegrityError):
        with seeded.begin() as connection:
            connection.exec_driver_sql("INSERT INTO obligations(entry_id,number,amount_cents,scheduled_date,invoice_id) SELECT entry_id,number,amount_cents,scheduled_date,invoice_id FROM obligations WHERE entry_id=? AND number=1",(identity,))


def test_non_credit_installment_cannot_be_assigned_to_invoice(seeded):
    credit=record_entry(seeded,draft())
    pix=record_entry(seeded,draft(request_key="pix",payment_method="pix",card_id=None))
    with Session(seeded) as session:
        invoice=session.get(Entry,credit).obligations[0].invoice_id
    with pytest.raises(IntegrityError):
        with seeded.begin() as connection:
            connection.exec_driver_sql("UPDATE obligations SET invoice_id=? WHERE entry_id=?",(invoice,pix))


def test_failed_migration_rolls_back_ddl_as_well_as_revision(tmp_path):
    path=tmp_path/"failed-migration.db"
    engine=make_engine(path)
    with pytest.raises(RuntimeError):
        with engine.begin() as connection:
            config=migration_config()
            config.attributes["connection"]=connection
            command.upgrade(config,"0001_initial")
            raise RuntimeError("Falha após DDL; nenhuma tabela deve permanecer")
    with engine.connect() as connection:
        assert inspect(connection).get_table_names()==[]
    engine.dispose()


def test_reapplying_head_preserves_existing_data(seeded):
    identity=record_entry(seeded,draft())
    upgrade_database(seeded.url.database)
    with Session(seeded) as session:
        entry=session.get(Entry,identity)
        assert entry.total_cents==10000
        assert len(entry.obligations)==3
        assert count(session,Invoice)==3

def test_migration_cli_works_outside_project_directory(tmp_path):
    import subprocess,sys,os
    path=tmp_path/"outside-cli.db"
    env=dict(os.environ);env.pop("PYTHONPATH",None)
    result=subprocess.run([sys.executable,"-m","app.persistence","upgrade","--database",str(path)],cwd=tmp_path,env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert current_revision(path)==head_revision()
