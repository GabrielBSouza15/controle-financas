"""Comandos de recuperação local. Restaurar sempre cria um arquivo novo."""
import argparse
import json
from pathlib import Path
from app.persistence.database import DEFAULT_DATABASE_PATH
from app.services.backup import DEFAULT_BACKUP_ROOT, create_backup, verify_backup, restore_backup
from app.persistence.entries import PersistenceValidationError
from app.services.catalogs import ConflictError, NotFoundError


def main():
    parser = argparse.ArgumentParser(description="Backup consistente e restauração para arquivo novo.")
    sub = parser.add_subparsers(dest="operation", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    backup.add_argument("--destination-dir", type=Path, default=DEFAULT_BACKUP_ROOT)
    verify = sub.add_parser("verify")
    verify.add_argument("--backup", type=Path, required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.operation == "backup":
            result = create_backup(args.database, args.destination_dir)
        elif args.operation == "verify":
            result = verify_backup(args.backup)
        else:
            result = restore_backup(args.backup, args.destination)
    except (PersistenceValidationError, ConflictError, NotFoundError, OSError) as exc:
        parser.exit(1, f"Operação não concluída: {exc}\n")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
