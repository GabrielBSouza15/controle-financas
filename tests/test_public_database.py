from hashlib import sha256
import sqlite3
import pytest
from scripts.prepare_database import prepare


def test_empty_has_no_business_records_and_existing_file_is_preserved(tmp_path):
    path = tmp_path / 'empty.db'
    prepare('empty', path)
    before = sha256(path.read_bytes()).hexdigest()
    with sqlite3.connect(path) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        assert all(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] == 0
                   for name in tables if name != 'alembic_version')
    with pytest.raises(FileExistsError):
        prepare('demo',path,'2026-10')
    assert sha256(path.read_bytes()).hexdigest() == before


def test_demo_is_synthetic_without_historical_imports_or_duplicate_income(tmp_path):
    path = tmp_path / 'demo.db'
    prepare('demo',path,'2026-12')
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM entries').fetchone()[0] == 5
        assert conn.execute('SELECT COUNT(*) FROM recurrence_series').fetchone()[0] == 3
        assert conn.execute('SELECT COUNT(*) FROM history_records').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM financial_events').fetchone()[0] == 0
        assert conn.execute("SELECT start_on FROM recurrence_series WHERE kind='income'").fetchone()[0] == '2027-01-01'
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
