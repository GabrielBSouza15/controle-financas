"""Ferramenta local de estrutura; não importa dados nem confirma pagamentos."""

import argparse
from pathlib import Path
from .database import DEFAULT_DATABASE_PATH
from .migrations import current_revision, upgrade_database


def main() -> None:
    parser = argparse.ArgumentParser(description="Estrutura local do banco financeiro.")
    parser.add_argument("operation", choices=["upgrade", "status"])
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    args = parser.parse_args()
    if args.operation == "upgrade":
        upgrade_database(args.database)
    print(f"Banco: {args.database.resolve()}")
    print(f"Revisão: {current_revision(args.database) or 'não inicializado'}")


if __name__ == "__main__":
    main()
