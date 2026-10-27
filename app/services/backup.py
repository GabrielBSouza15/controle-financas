"""Backups consistentes e restauração para arquivo novo, sem substituir o banco ativo."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4
import json
import os
import sqlite3
import re
import time
from contextlib import closing
from alembic.script import ScriptDirectory
from app.persistence.database import PROJECT_ROOT
from app.persistence.entries import PersistenceValidationError
from app.persistence.migrations import migration_config
from .catalogs import ConflictError, NotFoundError

DEFAULT_BACKUP_ROOT = PROJECT_ROOT / "backups"
FORMAT_VERSION = 1


def operation_id(value=None):
    try:
        return str(UUID(str(value))) if value is not None else str(uuid4())
    except (ValueError, AttributeError):
        raise PersistenceValidationError("Informe um identificador UUID de backup válido.") from None


def file_hash(path):
    digest = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def readonly(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise NotFoundError("O arquivo de banco não foi encontrado.")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA trusted_schema=OFF")
    return conn


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def database_fingerprint(path):
    """Conferir estrutura e conteúdo exato sem registrar descrições ou valores no relatório."""
    try:
        with closing(readonly(path)) as conn:
            conn.execute("BEGIN")
            if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise PersistenceValidationError("A integridade do banco não foi confirmada.")
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise PersistenceValidationError("O banco contém relacionamentos inconsistentes.")
            schema = conn.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            ).fetchall()
            tables = sorted(row[1] for row in schema if row[0] == "table")
            if not {"alembic_version", "entries", "obligations", "invoices", "responsibles", "cards"}.issubset(tables):
                raise PersistenceValidationError("O arquivo não tem a estrutura do controle financeiro.")
            revisions = conn.execute("SELECT version_num FROM alembic_version").fetchall()
            known = {revision.revision for revision in ScriptDirectory.from_config(migration_config()).walk_revisions()}
            if len(revisions) != 1 or revisions[0][0] not in known:
                raise PersistenceValidationError("A revisão deste banco não é reconhecida por esta instalação.")
            result = {}
            for name in tables:
                digest = sha256()
                count = 0
                for row in conn.execute("SELECT * FROM " + quoted(name) + " ORDER BY rowid"):
                    values = [{"blob_hex": value.hex()} if isinstance(value, bytes) else value for value in row]
                    digest.update(json.dumps(values, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii") + b"\n")
                    count += 1
                result[name] = {"count": count, "sha256": digest.hexdigest()}
            return {
                "revision": revisions[0][0], "tables": result,
                "schema_sha256": sha256(json.dumps(schema, ensure_ascii=True, separators=(",", ":")).encode("ascii")).hexdigest(),
            }
    except sqlite3.Error:
        raise PersistenceValidationError("Não foi possível validar o banco SQLite; nada foi adotado.") from None


def _load_manifest(folder):
    if not folder.is_dir():
        raise NotFoundError("A pasta de backup não foi encontrada.")
    path = folder / "manifest.json"
    if not path.is_file():
        raise ConflictError("A cópia ainda não foi concluída ou não possui manifesto.")
    if path.stat().st_size > 1024 * 1024:
        raise PersistenceValidationError("O manifesto excede o limite permitido.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or type(data.get("format_version")) is not int or data.get("format_version") != FORMAT_VERSION
                or data.get("database_file") != "database.db"
                or not isinstance(data.get("fingerprint"), dict)
                or not isinstance(data.get("database_sha256"), str)):
            raise ValueError
        if operation_id(data.get("id", "invalid")) != data.get("id"):
            raise ValueError
        if not isinstance(data.get("source_path"), str) or not data["source_path"]:
            raise ValueError
        if not isinstance(data.get("created_at"), str) or datetime.fromisoformat(data["created_at"]).tzinfo is None:
            raise ValueError
        fingerprint = data["fingerprint"]
        if set(fingerprint) != {"revision", "tables", "schema_sha256"} or not isinstance(fingerprint["revision"], str):
            raise ValueError
        hashes = [data["database_sha256"], fingerprint["schema_sha256"]]
        if not isinstance(fingerprint["tables"], dict) or not 1 <= len(fingerprint["tables"]) <= 1000:
            raise ValueError
        for name, value in fingerprint["tables"].items():
            if (not isinstance(name, str) or not isinstance(value, dict)
                    or set(value) != {"count", "sha256"} or type(value["count"]) is not int or value["count"] < 0):
                raise ValueError
            hashes.append(value["sha256"])
        if any(not isinstance(value, str) or re.fullmatch("[0-9a-f]{64}", value) is None for value in hashes):
            raise ValueError
        return data
    except (ValueError, TypeError, UnicodeError):
        raise PersistenceValidationError("O manifesto de backup é inválido.") from None


def bundle_info(folder, manifest, *, verified=False):
    return {
        "id": manifest["id"], "created_at": manifest["created_at"], "folder": str(folder),
        "database_path": str(folder / "database.db"), "manifest_path": str(folder / "manifest.json"),
        "source_database_name": Path(manifest["source_path"]).name,
        "revision": manifest["fingerprint"]["revision"], "database_sha256": manifest["database_sha256"],
        "table_counts": {name: row["count"] for name, row in manifest["fingerprint"]["tables"].items()},
        "verified": verified,
    }


def verify_backup(folder):
    folder = Path(folder).resolve()
    manifest = _load_manifest(folder)
    database = folder / "database.db"
    if not database.is_file():
        raise NotFoundError("O banco desta cópia não foi encontrado.")
    if file_hash(database) != manifest["database_sha256"]:
        raise PersistenceValidationError("O arquivo diverge do hash do backup. Não use esta cópia.")
    actual = database_fingerprint(database)
    if actual != manifest["fingerprint"] or file_hash(database) != manifest["database_sha256"]:
        raise PersistenceValidationError("Conteúdo ou estrutura divergente do manifesto. Nada foi restaurado.")
    return bundle_info(folder, manifest, verified=True)


def create_backup(source, root=DEFAULT_BACKUP_ROOT, *, request_key=None):
    source = Path(source).resolve()
    if not source.is_file():
        raise NotFoundError("O banco de origem não foi encontrado.")
    root = Path(root).resolve()
    identity = operation_id(request_key)
    folder = root / ("finance-" + identity)
    if source.is_relative_to(folder):
        raise PersistenceValidationError("O destino do backup não pode conter o banco ativo.")
    root.mkdir(parents=True, exist_ok=True)
    try:
        folder.mkdir()
    except FileExistsError:
        manifest = _load_manifest(folder)
        if manifest.get("source_path") != str(source):
            raise ConflictError("Este identificador já foi usado para outra origem.")
        return verify_backup(folder)
    database = folder / "database.db"
    try:
        # A pasta é exclusiva desta operação; a API SQLite inclui dados confirmados em WAL.
        with database.open("xb"):
            pass
        copy_sqlite(source, database)
        fingerprint = database_fingerprint(database)
        manifest = {
            "format_version": FORMAT_VERSION, "id": identity,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_path": str(source), "database_file": "database.db",
            "database_sha256": file_hash(database), "fingerprint": fingerprint,
        }
        with (folder / "manifest.json").open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=True, indent=2)
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        return verify_backup(folder)
    except sqlite3.Error:
        raise PersistenceValidationError("O backup não foi concluído. A origem foi preservada; não use a pasta incompleta.") from None


def list_backups(root=DEFAULT_BACKUP_ROOT, *, limit=20):
    root = Path(root).resolve()
    if not root.exists():
        return {"items": [], "incomplete_count": 0}
    items = []
    incomplete = 0
    for folder in root.glob("finance-*"):
        if not folder.is_dir():
            continue
        try:
            manifest = _load_manifest(folder)
            if folder.name != "finance-" + manifest["id"]:
                raise ValueError
            items.append(bundle_info(folder, manifest))
        except (OSError, ValueError, KeyError, ConflictError, NotFoundError, PersistenceValidationError):
            incomplete += 1
    items.sort(key=lambda row: (row["created_at"], row["id"]), reverse=True)
    return {"items": items[:limit], "incomplete_count": incomplete, "total": len(items)}


def restore_backup(folder, destination):
    """Produzir candidato novo. Nunca apagar/substituir banco, WAL ou arquivo existente."""
    folder = Path(folder).resolve()
    info = verify_backup(folder)
    destination = Path(destination)
    if destination.is_symlink():
        raise ConflictError("O destino e um link existente. Escolha outro nome.")
    destination = destination.resolve()
    if destination.exists() or destination.is_symlink():
        raise ConflictError("O destino já existe. Escolha outro nome; nenhum arquivo foi substituído.")
    if any(Path(str(destination) + suffix).exists() or Path(str(destination) + suffix).is_symlink()
           for suffix in ("-wal", "-shm", "-journal")):
        raise ConflictError("Ha arquivos auxiliares SQLite neste destino. Preserve-os e escolha outro nome.")
    if destination.is_relative_to(folder):
        raise PersistenceValidationError("Restaure fora da pasta da cópia de segurança.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb"):
        pass
    try:
        copy_sqlite(folder / "database.db", destination)
        expected = _load_manifest(folder)["fingerprint"]
        if database_fingerprint(destination) != expected:
            raise PersistenceValidationError("A restauração não corresponde ao backup; não adote o candidato.")
        verify_backup(folder)
    except sqlite3.Error:
        raise PersistenceValidationError("A restauração não foi concluída; a origem e o banco ativo foram preservados.") from None
    return {"destination": str(destination), "verified": True, "revision": info["revision"],
            "active_database_replaced": False, "table_counts": info["table_counts"]}


def copy_sqlite(source, destination):
    deadline = time.monotonic() + 30
    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise ConflictError("O banco permaneceu ocupado. Tente novamente; nenhuma origem foi substituída.")
    with closing(readonly(source)) as origin, closing(sqlite3.connect(destination)) as target:
        origin.backup(target, pages=256, progress=progress, sleep=0.05)
