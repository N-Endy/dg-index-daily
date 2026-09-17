"""Regression: refuse football-data-style loopback redirects; archive fallback."""
from __future__ import annotations

import gzip
import socket
import threading
from pathlib import Path
from typing import Any, Dict, List

import pytest

import dg.http as http_mod
from dg.http import UnsafeRedirectError, fetch
from dg.sources import footballdata as fd


class _AcceptCounter:
    """TCP listener that counts accept() calls without serving HTTP."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(5)
        self.port = int(self._sock.getsockname()[1])
        self.accepted = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        self._sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
                self.accepted += 1
                conn.close()
            except socket.timeout:
                continue
            except OSError:
                break

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=2)


class _FakeResponse:
    def __init__(self, status_code: int, headers: Dict[str, str], content: bytes = b""):
        self.status_code = status_code
        self.headers = headers
        self.content = content
        self.text = content.decode("latin-1", errors="replace")


@pytest.fixture
def reset_http_session(monkeypatch):
    prev = http_mod._SESSION
    prev_wait = http_mod._last_request_at
    http_mod._SESSION = None
    http_mod._last_request_at = 0.0
    monkeypatch.setattr("dg.config.REQUEST_DELAY_SEC", 0.0)
    yield
    http_mod._SESSION = prev
    http_mod._last_request_at = prev_wait


def test_fetch_refuses_loopback_redirect(monkeypatch, reset_http_session):
    """Reproduce www → apex → http://127.0.0.1/... without dialing loopback."""
    counter = _AcceptCounter()
    counter.start()
    www = "https://www.football-data.co.uk/mmz4281/2627/E0.csv"
    apex = "https://football-data.co.uk/mmz4281/2627/E0.csv"
    loopback = f"http://127.0.0.1:{counter.port}/mmz4281/2627/E0.csv"
    seen: List[str] = []

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        seen.append(url)
        assert kwargs.get("allow_redirects") is False
        if url == www:
            return _FakeResponse(302, {"Location": apex})
        if url == apex:
            return _FakeResponse(302, {"Location": loopback})
        raise AssertionError(f"unexpected GET {url}")

    session = http_mod.get_session()
    monkeypatch.setattr(session, "get", fake_get)

    try:
        with pytest.raises(UnsafeRedirectError) as ei:
            fetch(www)
        err = ei.value
        assert err.from_url == apex
        assert err.location == loopback
        assert seen == [www, apex]
        assert counter.accepted == 0
    finally:
        counter.stop()


def test_iter_main_leagues_circuit_breaks(monkeypatch, reset_http_session):
    calls = {"n": 0}

    def fake_fetch(url: str, **kwargs: Any):
        calls["n"] += 1
        raise UnsafeRedirectError(url, "http://127.0.0.1/mmz4281/2627/E0.csv")

    monkeypatch.setattr(fd, "fetch", fake_fetch)
    monkeypatch.setattr("dg.config.FD_MAIN_CODES", ("E0", "E1", "SP1"))
    monkeypatch.setattr(fd.config, "RAW_DIR", Path("/nonexistent-raw-dir-xyz"))

    results = list(fd.iter_main_leagues("2627"))
    assert results == []
    assert calls["n"] == 1  # circuit break after first UnsafeRedirectError


def test_iter_main_leagues_archive_fallback(monkeypatch, tmp_path, reset_http_session):
    raw = tmp_path / "raw" / "2026-09-17"
    raw.mkdir(parents=True)
    csv_body = b"Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\nE0,01/01/26,A,B,1,0\n"
    with gzip.open(raw / "fd_2627_E0.csv.gz", "wb") as f:
        f.write(csv_body)
    monkeypatch.setattr(fd.config, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr("dg.config.FD_MAIN_CODES", ("E0", "E1"))

    def fake_fetch(url: str, **kwargs: Any):
        raise UnsafeRedirectError(url, "http://127.0.0.1/mmz4281/2627/E0.csv")

    monkeypatch.setattr(fd, "fetch", fake_fetch)

    results = list(fd.iter_main_leagues("2627"))
    assert len(results) == 1
    code, rows = results[0]
    assert code == "E0"
    assert rows[0]["HomeTeam"] == "A"
    assert rows[0]["AwayTeam"] == "B"
