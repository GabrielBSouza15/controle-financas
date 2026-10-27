"""Inicialização pelo atalho: terminal para Ctrl+C e navegador após o servidor ficar pronto."""
import argparse
import json
from pathlib import Path
import socket
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import uvicorn
from sqlalchemy.exc import SQLAlchemyError
from app.local_identity import APPLICATION_ID, database_id
from app.main import create_app
from app.persistence.database import DEFAULT_DATABASE_PATH
from app.persistence.migrations import current_revision, head_revision


class LaunchError(Exception):
    pass


def probe_local_server(port, database_path):
    """Não reutilizar outro serviço ou outro banco que esteja ocupando a mesma porta."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
            data = json.loads(response.read(4096))
        return (isinstance(data, dict) and data.get("application") == APPLICATION_ID
                and data.get("database_id") == database_id(database_path)
                and data.get("revision") == head_revision())
    except (OSError, ValueError, urllib.error.URLError):
        return False


def launch(database_path=DEFAULT_DATABASE_PATH, port=8000, *, open_browser=True):
    database_path = Path(database_path).resolve()
    try:
        revision = current_revision(database_path) if database_path.is_file() else None
    except SQLAlchemyError:
        raise LaunchError("Banco ilegivel ou corrompido. Preserve o arquivo e consulte o guia de recuperacao.") from None
    if revision != head_revision():
        raise LaunchError("Banco ausente ou desatualizado. Consulte o guia de operação; a abertura não cria nem migra dados.")
    url = f"http://127.0.0.1:{port}/"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            if probe_local_server(port, database_path):
                print("A aplicação já está aberta para este banco. Use o terminal original para encerrá-la.")
                if open_browser:
                    webbrowser.open(url)
                return "reused"
            raise LaunchError(f"A porta {port} está ocupada por outro serviço ou outra versão. Encerre o servidor antigo ou escolha --port.") from None
        listener.listen(128)
        config = uvicorn.Config(create_app(database_path), host="127.0.0.1", port=port,
                                access_log=False, proxy_headers=False)
        server = uvicorn.Server(config)
        finished = threading.Event()

        def open_when_ready():
            deadline = time.monotonic() + 20
            while not finished.wait(0.1) and time.monotonic() < deadline:
                if server.started:
                    if not webbrowser.open(url):
                        print("Abra o navegador neste endereço:", url)
                    return

        thread = threading.Thread(target=open_when_ready, daemon=True) if open_browser else None
        print("Controle de finanças — uso local")
        print("Endereço:", url)
        print("Mantenha este terminal aberto. Para encerrar, pressione Ctrl+C.")
        if thread:
            thread.start()
        try:
            server.run(sockets=[listener])
        except KeyboardInterrupt:
            print("Aplicação encerrada.")
        finally:
            finished.set()
            if thread:
                thread.join(timeout=1)
        return "stopped"


def main():
    parser = argparse.ArgumentParser(description="Abrir o controle financeiro e o navegador local.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--wait-on-error", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Escolha uma porta entre 1 e 65535.")
    try:
        launch(args.database, args.port, open_browser=not args.no_browser)
    except (LaunchError, OSError) as exc:
        print(f"Não foi possível abrir: {exc}")
        if args.wait_on_error:
            try:
                input("Pressione Enter para fechar esta janela. ")
            except EOFError:
                pass
        parser.exit(1)


if __name__ == "__main__":
    main()
