"""Shared HTTP session with retries, polite delay, and conditional GET."""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from dg import config

logger = logging.getLogger(__name__)

_last_request_at = 0.0


@dataclass
class HttpResponse:
    url: str
    status_code: int
    content: bytes
    text: str
    headers: Dict[str, str]
    not_modified: bool = False

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": config.USER_AGENT, "Accept": "*/*"})
    retry = Retry(
        total=config.MAX_RETRIES,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


_SESSION: Optional[requests.Session] = None


def get_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = _session()
    return _SESSION


def _polite_wait() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < config.REQUEST_DELAY_SEC:
        time.sleep(config.REQUEST_DELAY_SEC - elapsed)
    _last_request_at = time.monotonic()


def fetch(
    url: str,
    *,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
) -> HttpResponse:
    """GET with optional conditional headers. Raises on hard HTTP failures."""
    _polite_wait()
    headers: Dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    logger.debug("GET %s", url)
    resp = get_session().get(url, headers=headers, timeout=config.REQUEST_TIMEOUT_SEC)
    if resp.status_code == 304:
        return HttpResponse(
            url=url,
            status_code=304,
            content=b"",
            text="",
            headers=dict(resp.headers),
            not_modified=True,
        )
    if resp.status_code >= 400:
        raise requests.HTTPError(
            f"HTTP {resp.status_code} for {url}",
            response=resp,
        )
    return HttpResponse(
        url=url,
        status_code=resp.status_code,
        content=resp.content,
        text=resp.text,
        headers={k: v for k, v in resp.headers.items()},
    )


def fetch_bytes(url: str) -> Tuple[bytes, str]:
    """Convenience: return (content, sha256)."""
    r = fetch(url)
    return r.content, r.sha256
