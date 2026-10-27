"""Conexões locais com integridade referencial e transações explícitas."""

from contextlib import contextmanager
from pathlib import Path
from sqlalchemy import URL, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "finance-active.db"


def make_engine(path: str | Path = DEFAULT_DATABASE_PATH) -> Engine:
    """Não abre conexão nem cria arquivo; o diretório deve existir."""
    path = Path(path).resolve()
    engine = create_engine(URL.create("sqlite", database=str(path)), connect_args={"timeout": 10}, hide_parameters=True)

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, _):
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def begin_transaction(connection):
        connection.exec_driver_sql("BEGIN")

    return engine


@contextmanager
def transaction(engine: Engine):
    """Contexto: commit na saída normal, rollback completo em exceção."""
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            yield session
