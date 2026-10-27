"""Inicialização e atalho sem abrir navegador real nos testes."""
from pathlib import Path
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from app.launch import LaunchError, launch, probe_local_server
from app.local_identity import APPLICATION_ID, database_id
from app.persistence.migrations import head_revision, upgrade_database


def test_missing_database_does_not_initialize_it(tmp_path):
    path = tmp_path / "absent.db"
    with pytest.raises(LaunchError):
        launch(path, open_browser=False)
    assert not path.exists()


def test_health_identity_exposes_no_financial_data(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"application": APPLICATION_ID,
                               "database_id": database_id(client.app.state.database_path), "revision": head_revision()}
    assert str(client.app.state.database_path) not in response.text
    assert response.headers["cache-control"] == "no-store"


# Importar somente a fixture, sem repetir testes de outro módulo.
from test_settlements import client


def test_launcher_reuses_only_matching_application_and_database(tmp_path):
    path = tmp_path / "local.db"; upgrade_database(path)
    document = {"application": APPLICATION_ID, "database_id": database_id(path), "revision": head_revision()}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            content = json.dumps(document).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(content))); self.end_headers(); self.wfile.write(content)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    port = server.server_address[1]
    try:
        assert probe_local_server(port, path)
        assert launch(path, port, open_browser=False) == "reused"
        document["database_id"] = database_id(tmp_path / "different.db")
        with pytest.raises(LaunchError, match="ocupada"):
            launch(path, port, open_browser=False)
        document["application"] = "other-app"
        assert not probe_local_server(port, path)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_real_launch_cli_starts_on_loopback_without_initializing_or_opening_browser(tmp_path):
    path = tmp_path / "live.db"; upgrade_database(path)
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0)); port = available.getsockname()[1]
    output = tmp_path / "server-output.txt"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with output.open("w", encoding="utf-8") as stream:
        proc = subprocess.Popen([sys.executable, "-m", "app.launch", "--database", str(path),
                                 "--port", str(port), "--no-browser"],
                                cwd=tmp_path, stdout=stream, stderr=stream, creationflags=flags)
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not probe_local_server(port, path):
                assert proc.poll() is None, output.read_text(encoding="utf-8")
                time.sleep(0.05)
            assert probe_local_server(port, path)
            reused = subprocess.run([sys.executable, "-m", "app.launch", "--database", str(path),
                                     "--port", str(port), "--no-browser"], cwd=tmp_path,
                                    capture_output=True, timeout=10, creationflags=flags)
            assert reused.returncode == 0, reused.stderr
        finally:
            proc.terminate(); proc.wait(timeout=10)


@pytest.mark.skipif(os.name != "nt" or shutil.which("pwsh") is None, reason="Atalho exclusivo do Windows.")
def test_shortcut_is_local_and_does_not_overwrite_another_file(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "create_desktop_shortcut.ps1"
    # Banco fictício, e atalho somente na pasta temporária.
    database = tmp_path / "fictitious.db"; upgrade_database(database)
    command = ["pwsh", "-NoProfile", "-File", str(script), "-DestinationDirectory", str(tmp_path), "-Database", str(database)]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    links = list(tmp_path.glob("*.lnk")); assert len(links) == 1
    previous = links[0].read_bytes()
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, timeout=15)
    assert result.returncode == 0 and links[0].read_bytes() == previous
    links[0].write_bytes(b"unrelated file must remain")
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, timeout=15)
    assert result.returncode != 0
    assert links[0].read_bytes() == b"unrelated file must remain"


def test_browser_is_opened_only_after_server_started(tmp_path, monkeypatch):
    path = tmp_path / "browser-ready.db"; upgrade_database(path)
    opened = []
    import app.launch as module
    def browser_open(url):
        opened.append(url)
        return True
    def fake_run(server, sockets):
        assert opened == []
        server.started = True
        deadline = time.monotonic() + 3
        while not opened and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(opened) == 1
    monkeypatch.setattr(module.webbrowser, "open", browser_open)
    monkeypatch.setattr(module.uvicorn.Server, "run", fake_run)
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0)); port = available.getsockname()[1]
    assert launch(path, port) == "stopped"
    assert opened == [f"http://127.0.0.1:{port}/"]


def test_ctrl_c_is_normal_shutdown_without_traceback(tmp_path, monkeypatch, capsys):
    path = tmp_path / "interrupt.db"; upgrade_database(path)
    import app.launch as module
    def interrupt(server, sockets):
        raise KeyboardInterrupt
    monkeypatch.setattr(module.uvicorn.Server, "run", interrupt)
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0)); port = available.getsockname()[1]
    assert launch(path, port, open_browser=False) == "stopped"
    captured = capsys.readouterr()
    assert "encerrada" in captured.out
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("document", [[], None, "other service", 42])
def test_probe_rejects_non_object_json(tmp_path, monkeypatch, document):
    import io
    import app.launch as module
    class Opener:
        def open(self, *args, **kwargs):
            return io.BytesIO(json.dumps(document).encode())
    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *args: Opener())
    assert probe_local_server(8000, tmp_path / "unused.db") is False


def test_corrupt_database_has_actionable_launch_error(tmp_path):
    path = tmp_path / "corrupt.db"
    content = b"This is not a SQLite database" * 100
    path.write_bytes(content)
    with pytest.raises(LaunchError):
        launch(path, open_browser=False)
    assert path.read_bytes() == content
