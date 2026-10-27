"""Identificação técnica sem caminhos ou dados financeiros na resposta HTTP."""
from hashlib import sha256
from pathlib import Path

APPLICATION_ID = "controle-financas-local"


def database_id(path):
    return sha256(str(Path(path).resolve()).casefold().encode("utf-8")).hexdigest()
