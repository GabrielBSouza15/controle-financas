"""Usa o mesmo controle de conexão/transação da aplicação."""

from alembic import context
from app.persistence.database import DEFAULT_DATABASE_PATH, make_engine
from app.persistence.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url="sqlite:///data/finance.db", target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    shared = config.attributes.get("connection")
    if shared is not None:
        run_migrations(shared)
    else:
        DEFAULT_DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        engine = make_engine(DEFAULT_DATABASE_PATH)
        try:
            with engine.begin() as connection:
                run_migrations(connection)
        finally:
            engine.dispose()
