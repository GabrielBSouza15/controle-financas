"""Aplicação de revisões Alembic em um caminho explícito."""

from pathlib import Path
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from .database import DEFAULT_DATABASE_PATH, PROJECT_ROOT, make_engine


def migration_config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def upgrade_database(path: str | Path = DEFAULT_DATABASE_PATH) -> None:
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(path)
    try:
        with engine.begin() as connection:
            config = migration_config()
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()


def current_revision(path: str | Path = DEFAULT_DATABASE_PATH) -> str | None:
    path = Path(path).resolve()
    if not path.is_file():
        return None
    engine = make_engine(path)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def head_revision():
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(migration_config()).get_current_head()
