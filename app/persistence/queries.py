"""Predicados financeiros compartilhados: histórico preservado não é valor ativo."""

from sqlalchemy import select
from .models import Entry, EntryChange


def active_entry_filter():
    """Expressão SQL, sem executar consulta ou alterar registros."""
    return ~Entry.id.in_(select(EntryChange.source_entry_id))
