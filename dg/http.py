"""Shared HTTP session with retries, polite delay, and conditional GET."""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from dg import config

logger = logging.getLogger(__name__)

_last_request_at = 0.0
_MAX_REDIRECTS = 5
_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})


class UnsafeRedirectError(requests.RequestException):
    """Raised when a redirect Location targets a private/loopback host."""

    def __init__(self, from_url: str, location: str):
        self.from_url = from_url
        self.location = location
        super().__init__(f"refusing redirect {from_url} -> {location}")


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


# Explicitly disable proxies so redirects cannot re-apply HTTP(S)_PROXY.
_NO_PROXIES = {"http": None, "https": None}


def _is_unsafe_hostname(hostname: Optional[str]) -> bool:
    """True for loopback, link-local, RFC1918, or unresolved localhost names."""
    if not hostname:
        return True
    host = hostname.strip("[]").lower()
    if host in {"localhost", "localhost.localdomain"}:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Non-IP hostnames are allowed (e.g. football-data.co.uk).
        return False
    return bool(
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
        or ip.is_unspecified
    )


def _session() -> requests.Session:
    s = requests.Session()
    # Ignore HTTP_PROXY / HTTPS_PROXY. Railway (or a local shell) may set a
    # dead localhost proxy; football-data.co.uk HTTPS→HTTP redirects then fail
    # with Connection refused to 127.0.0.1:80.
    s.trust_env = False
    s.proxies.update(_NO_PROXIES)
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
    """GET with optional conditional headers. Raises on hard HTTP failures.

    Redirects are followed manually so loopback/private Locations (e.g.
    football-data.co.uk → http://127.0.0.1/...) are refused before any TCP
    connect, avoiding urllib3 retry storms against a dead loopback port.
    """
    headers: Dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    session = get_session()
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        _polite_wait()
        logger.debug("GET %s", current)
        # Pass proxies on each GET so redirect hops cannot re-resolve env proxies.
        resp = session.get(
            current,
            headers=headers,
            timeout=config.REQUEST_TIMEOUT_SEC,
            proxies=_NO_PROXIES,
            allow_redirects=False,
        )
        if resp.status_code in _REDIRECT_STATUS:
            location = resp.headers.get("Location")
            if not location:
                raise requests.HTTPError(
                    f"HTTP {resp.status_code} redirect without Location for {current}",
                    response=resp,
                )
            next_url = urljoin(current, location)
            host = urlparse(next_url).hostname
            if _is_unsafe_hostname(host):
                raise UnsafeRedirectError(current, next_url)
            # Conditional headers apply only to the original URL.
            headers = {}
            current = next_url
            continue

        if resp.status_code == 304:
            return HttpResponse(
                url=current,
                status_code=304,
                content=b"",
                text="",
                headers=dict(resp.headers),
                not_modified=True,
            )
        if resp.status_code >= 400:
            raise requests.HTTPError(
                f"HTTP {resp.status_code} for {current}",
                response=resp,
            )
        return HttpResponse(
            url=current,
            status_code=resp.status_code,
            content=resp.content,
            text=resp.text,
            headers={k: v for k, v in resp.headers.items()},
        )

    raise requests.TooManyRedirects(f"Exceeded {_MAX_REDIRECTS} redirects for {url}")


def fetch_bytes(url: str) -> Tuple[bytes, str]:
    """Convenience: return (content, sha256)."""
    r = fetch(url)
    return r.content, r.sha256
