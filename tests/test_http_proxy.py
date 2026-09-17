"""Regression: fetch must ignore a dead localhost HTTP(S)_PROXY."""
from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import dg.http as http_mod
from dg.http import fetch, get_session


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _OkHandler(BaseHTTPRequestHandler):
    body = b"Div,Date,HomeTeam,AwayTeam\nE0,01/01/26,A,B\n"

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/csv")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


@pytest.fixture
def reset_http_session():
    prev = http_mod._SESSION
    prev_wait = http_mod._last_request_at
    http_mod._SESSION = None
    http_mod._last_request_at = 0.0
    yield
    http_mod._SESSION = prev
    http_mod._last_request_at = prev_wait


@pytest.fixture
def local_csv_server():
    server = HTTPServer(("127.0.0.1", 0), _OkHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/mmz4281/2627/E0.csv"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_session_disables_env_proxy(reset_http_session):
    s = get_session()
    assert s.trust_env is False
    assert s.proxies.get("http") is None
    assert s.proxies.get("https") is None


def test_fetch_ignores_dead_http_proxy(
    monkeypatch, reset_http_session, local_csv_server
):
    dead = _free_port()
    # Bind nothing on dead — Connection refused if anything dials it.
    proxy_url = f"http://127.0.0.1:{dead}"
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(key, proxy_url)
    monkeypatch.setattr("dg.config.REQUEST_DELAY_SEC", 0.0)

    # Confirm the proxy port is closed.
    with pytest.raises(OSError):
        with socket.create_connection(("127.0.0.1", dead), timeout=0.2):
            pass

    resp = fetch(local_csv_server)
    assert resp.status_code == 200
    assert b"HomeTeam" in resp.content
    assert "127.0.0.1" in resp.url  # hit our server, not a proxy rewrite
