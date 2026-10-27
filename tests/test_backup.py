"""Recuperação usa bancos fictícios e nunca substitui o banco pessoal."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
import json
import sqlite3
import subprocess
import sys
import pytest
from test_settlements import client, entry
from test_legacy_web import legacy_client
from app.services.backup import (
    create_backup, verify_backup, restore_backup, database_fingerprint, file_hash, list_backups,
)
from app.services.catalogs import ConflictError, NotFoundError
from app.persistence.entries import PersistenceValidationError


def backup(c, tmp_path, **kw):
    return create_backup(c.app.state.database_path, tmp_path / "copies", **kw)


def test_backup_restore_preserve_schema_rows_and_source(client, tmp_path):
    entry(client, total_cents=10001, installment_count=3)
    source = client.app.state.database_path
    before = file_hash(source)
    data = backup(client, tmp_path)
    assert data["verified"] and data["table_counts"]["entries"] == 1
    assert data["table_counts"]["obligations"] == 3
    assert file_hash(source) == before
    restored = tmp_path / "restored" / "candidate.db"
    result = restore_backup(data["folder"], restored)
    assert result["verified"] and result["active_database_replaced"] is False
    assert database_fingerprint(source) == database_fingerprint(restored)
    assert file_hash(source) == before


def test_repeated_key_preserves_same_snapshot_without_creating_another(client, tmp_path):
    key = str(uuid4())
    data = backup(client, tmp_path, request_key=key)
    entry(client)
    repeated = backup(client, tmp_path, request_key=key)
    assert repeated == data and repeated["table_counts"]["entries"] == 0
    assert len(list((tmp_path / "copies").glob("finance-*"))) == 1
    assert backup(client, tmp_path)["table_counts"]["entries"] == 1


def test_concurrent_same_key_creates_one_complete_copy(client, tmp_path):
    key = str(uuid4())
    def operation():
        try:
            return backup(client, tmp_path, request_key=key)
        except ConflictError:
            return None  # A segunda requisição pode encontrar a criação em andamento.
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: operation(), range(2)))
    assert any(result and result["verified"] for result in results)
    assert backup(client, tmp_path, request_key=key)["verified"]
    assert len(list((tmp_path / "copies").glob("finance-*"))) == 1


def test_backup_includes_committed_wal_and_excludes_uncommitted_changes(client, tmp_path):
    source = client.app.state.database_path
    conn = sqlite3.connect(source)
    try:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        conn.execute("UPDATE accounts SET name='Confirmada em WAL' WHERE id=?", (client.account,))
        conn.commit()
        assert Path(str(source) + "-wal").is_file()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE accounts SET name='Nao confirmada' WHERE id=?", (client.account,))
        data = backup(client, tmp_path)
        with sqlite3.connect(data["database_path"]) as copied:
            assert copied.execute("SELECT name FROM accounts WHERE id=?", (client.account,)).fetchone()[0] == "Confirmada em WAL"
        conn.rollback()
    finally:
        conn.close()


def test_restore_never_overwrites_existing_file(client, tmp_path):
    data = backup(client, tmp_path)
    destination = tmp_path / "existing.db"
    destination.write_bytes(b"preserve this file")
    with pytest.raises(ConflictError):
        restore_backup(data["folder"], destination)
    assert destination.read_bytes() == b"preserve this file"


def test_restore_rejects_destination_inside_bundle(client, tmp_path):
    data = backup(client, tmp_path)
    with pytest.raises(PersistenceValidationError):
        restore_backup(data["folder"], Path(data["folder"]) / "candidate.db")


def test_corrupt_backup_is_rejected_before_destination_creation(client, tmp_path):
    data = backup(client, tmp_path)
    with Path(data["database_path"]).open("ab") as stream:
        stream.write(b"tampering")
    with pytest.raises(PersistenceValidationError, match="hash"):
        verify_backup(data["folder"])
    destination = tmp_path / "not-created.db"
    with pytest.raises(PersistenceValidationError):
        restore_backup(data["folder"], destination)
    assert not destination.exists()


@pytest.mark.parametrize("change", [
    {"format_version": 99}, {"format_version": True}, {"database_file": "../finance.db"}, {"fingerprint": {}},
    {"id": "invalid"}, {"created_at": None}, {"database_sha256": "wrong"},
])
def test_invalid_manifest_has_explicit_error(client, tmp_path, change):
    data = backup(client, tmp_path)
    path = Path(data["manifest_path"])
    manifest = json.loads(path.read_text())
    path.write_text(json.dumps(manifest | change), encoding="utf-8")
    with pytest.raises(PersistenceValidationError):
        verify_backup(data["folder"])
    assert list_backups(tmp_path / "copies")["incomplete_count"] == 1


def test_manifest_content_digest_is_checked_in_addition_to_file_hash(client, tmp_path):
    data = backup(client, tmp_path)
    path = Path(data["manifest_path"])
    manifest = json.loads(path.read_text())
    manifest["fingerprint"]["tables"]["entries"]["count"] += 1
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PersistenceValidationError, match="Conteúdo"):
        verify_backup(data["folder"])


def test_missing_or_unrelated_database_is_not_adopted(tmp_path):
    with pytest.raises(NotFoundError):
        create_backup(tmp_path / "missing.db", tmp_path / "copies")
    assert not (tmp_path / "missing.db").exists()
    unrelated = tmp_path / "unrelated.db"
    with sqlite3.connect(unrelated) as conn:
        conn.execute("CREATE TABLE sample (id INTEGER)")
    with pytest.raises(PersistenceValidationError):
        create_backup(unrelated, tmp_path / "copies")


def test_legacy_origin_and_unknown_settlement_survive_restore(legacy_client, tmp_path):
    data = backup(legacy_client, tmp_path)
    restored = tmp_path / "restored-trial.db"
    restore_backup(data["folder"], restored)
    assert database_fingerprint(restored) == database_fingerprint(legacy_client.app.state.database_path)
    with sqlite3.connect(restored) as conn:
        assert conn.execute("SELECT COUNT(*) FROM financial_events").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM legacy_source_rows").fetchone()[0] == 3


def test_http_backup_is_local_csrf_protected_and_accepts_no_paths(client, tmp_path):
    client.app.state.backup_root = tmp_path / "api-copies"
    before = file_hash(client.app.state.database_path)
    assert client.get("/api/maintenance/backups").json()["items"] == []
    key = str(uuid4())
    assert client.post("/api/maintenance/backups", json={"request_key": key, "source": "arbitrary.db"}).status_code == 422
    assert client.post("/api/maintenance/backups", json={"request_key": key}, headers={"X-CSRF-Token": "bad"}).status_code == 403
    response = client.post("/api/maintenance/backups", json={"request_key": key})
    assert response.status_code == 200, response.text
    assert response.json()["verified"]
    assert client.post("/api/maintenance/backups", json={"request_key": key}).json() == response.json()
    listing = client.get("/api/maintenance/backups").json()
    assert listing["total"] == 1 and listing["active_database_name"] == client.app.state.database_path.name
    assert client.get("/api/maintenance/backups/" + key).json()["verified"]
    assert client.post("/api/maintenance/restore", json={}).status_code == 404
    assert client.get("/api/maintenance/backups/not-a-uuid").status_code == 422
    assert file_hash(client.app.state.database_path) == before


def test_backup_cli_and_restore_work_outside_project_directory(client, tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-m", "app.maintenance", "backup", "--database",
                             str(client.app.state.database_path), "--destination-dir", str(tmp_path / "cli-copies")],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    bundle = json.loads(result.stdout)
    destination = tmp_path / "cli-restored.db"
    restored = subprocess.run([sys.executable, "-m", "app.maintenance", "restore", "--backup",
                               bundle["folder"], "--destination", str(destination)],
                              cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert restored.returncode == 0, restored.stderr
    assert database_fingerprint(destination) == database_fingerprint(client.app.state.database_path)


def test_one_key_cannot_silently_change_backup_source(client, tmp_path):
    key = str(uuid4())
    data = backup(client, tmp_path, request_key=key)
    candidate = tmp_path / "other-source.db"
    restore_backup(data["folder"], candidate)
    with pytest.raises(ConflictError, match="outra origem"):
        create_backup(candidate, tmp_path / "copies", request_key=key)
    assert verify_backup(data["folder"]) == data


def test_complete_financial_views_match_after_restore(client, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from test_invoice_credits import scenario, apply
    from test_recurrences import series
    from test_settlements import post, event, allocations
    source, payment, target = scenario(client)
    assert apply(client, source, target).status_code == 200
    parents = entry(client, request_key="parents-roundtrip", responsible_id=2, total_cents=10000)
    post(client, event(client, request_key="partial-repay", kind="repayment", invoice_id=None,
                       payer_id=2, amount_cents=1234, allocations=allocations(parents, 1234)))
    series(client, request_key="monthly-roundtrip")
    paths = ["/api/dashboard?month=2026-10&months=3", "/api/dashboard?month=2026-11&months=3",
             "/api/entries", "/api/invoices", "/api/settlements",
             "/api/projections?month_from=2026-10&months=3"]
    before = {path: client.get(path).json() for path in paths}
    data = backup(client, tmp_path)
    restored = tmp_path / "full-restored.db"
    restore_backup(data["folder"], restored)
    with TestClient(create_app(restored, backup_root=tmp_path / "restored-copies"),
                    base_url="http://127.0.0.1:8000") as recovered:
        assert {path: recovered.get(path).json() for path in paths} == before


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_restore_preserves_existing_sqlite_sidecars(client, tmp_path, suffix):
    data = backup(client, tmp_path)
    destination = tmp_path / "candidate.db"
    sidecar = Path(str(destination) + suffix)
    sidecar.write_bytes(b"existing recovery evidence")
    with pytest.raises(ConflictError):
        restore_backup(data["folder"], destination)
    assert not destination.exists()
    assert sidecar.read_bytes() == b"existing recovery evidence"
