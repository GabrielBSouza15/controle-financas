"""Servidor local: não aceita bind público ou proxy externo."""

import argparse
from pathlib import Path
import uvicorn
from app.main import create_app
from app.persistence.database import DEFAULT_DATABASE_PATH


def main():
    parser = argparse.ArgumentParser(description="Controle financeiro local")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("A porta deve estar entre 1 e 65535.")
    uvicorn.run(create_app(args.database), host="127.0.0.1", port=args.port, access_log=False, proxy_headers=False)


if __name__ == "__main__":
    main()
