"""Flashscore.mobi score scrape — ported from MatchPredictor WebScraperService."""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import unquote

from dg import config

logger = logging.getLogger(__name__)

SCORE_RE = re.compile(r"^(?P<home>\d{1,2})\s*[-:]\s*(?P<away>\d{1,2})")
_MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)

# Module-level cooldown after Cloudflare / repeated failures (MatchPredictor health tracker)
_cooldown_until_monotonic: float = 0.0


class FlashscoreBlockedError(RuntimeError):
    """Page looks like a bot challenge / Cloudflare block."""


class FlashscoreCooldownError(RuntimeError):
    """Scraper is cooling down after a recent block."""


class FlashscoreUnavailableError(RuntimeError):
    """Playwright or browser not available."""


def _record_cooldown(seconds: Optional[float] = None) -> None:
    global _cooldown_until_monotonic
    sec = float(seconds if seconds is not None else config.FLASHSCORE_COOLDOWN_SEC)
    _cooldown_until_monotonic = time.monotonic() + max(0.0, sec)


def _check_cooldown() -> None:
    remaining = _cooldown_until_monotonic - time.monotonic()
    if remaining > 0:
        raise FlashscoreCooldownError(
            f"Flashscore scraper in cooldown for another {remaining:.0f}s"
        )


def reset_cooldown() -> None:
    """Clear scraper cooldown (tests / ops)."""
    global _cooldown_until_monotonic
    _cooldown_until_monotonic = 0.0


def looks_like_challenge_page(page_source: str, title: str = "") -> bool:
    blob = f"{title}\n{page_source}".lower()
    markers = (
        "just a moment",
        "cf-browser-verification",
        "challenge-platform",
        "attention required",
        "cloudflare",
        "access denied",
        "captcha",
    )
    return any(m in blob for m in markers)


def parse_score_string(raw: str) -> Optional[Tuple[int, int]]:
    m = SCORE_RE.match((raw or "").strip())
    if not m:
        return None
    return int(m.group("home")), int(m.group("away"))


class _ScoreDataParser(HTMLParser):
    """
    Port of MatchPredictor ParseScoreDataHtml.

    Expects the inner HTML of #score-data: h4 leagues, span times,
    text nodes \"Home - Away\", a.fin / a.live scores.
    """

    def __init__(self, *, finished_only: bool = True) -> None:
        super().__init__(convert_charrefs=True)
        self.finished_only = finished_only
        self.current_league = ""
        self.rows: List[Dict[str, Any]] = []
        self._pending_time = ""
        self._pending_live_span = False
        self._capture_text_for_teams = False
        self._teams_buf = ""
        self._in_a = False
        self._a_class = ""
        self._a_text = ""
        self._capture_h4 = False
        self._h4_buf = ""
        self._capture_span = False
        self._span_buf = ""
        self._span_class = ""

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attrs_d = {k: (v or "") for k, v in attrs}
        if tag == "h4":
            self._capture_h4 = True
            self._h4_buf = ""
            return
        if tag == "span":
            self._capture_span = True
            self._span_buf = ""
            self._span_class = attrs_d.get("class", "")
            return
        if tag == "a":
            self._in_a = True
            self._a_class = attrs_d.get("class", "")
            self._a_text = ""
            return

    def handle_endtag(self, tag: str) -> None:
        if tag == "h4" and getattr(self, "_capture_h4", False):
            raw = (self._h4_buf or "").split("Standings")[0].strip()
            self.current_league = raw
            self._capture_h4 = False
            return
        if tag == "span" and getattr(self, "_capture_span", False):
            self._pending_time = (self._span_buf or "").strip()
            self._pending_live_span = self._span_class == "live"
            self._capture_span = False
            self._capture_text_for_teams = True
            self._teams_buf = ""
            return
        if tag == "a" and self._in_a:
            self._in_a = False
            self._maybe_emit_row()
            return

    def handle_data(self, data: str) -> None:
        if getattr(self, "_capture_h4", False):
            self._h4_buf = getattr(self, "_h4_buf", "") + data
            return
        if getattr(self, "_capture_span", False):
            self._span_buf = getattr(self, "_span_buf", "") + data
            return
        if self._in_a:
            self._a_text += data
            return
        if self._capture_text_for_teams:
            self._teams_buf += data

    def _maybe_emit_row(self) -> None:
        a_class = self._a_class
        is_live_anchor = a_class == "live"
        is_fin = a_class == "fin"
        is_live = self._pending_live_span or is_live_anchor
        if self.finished_only and not is_fin:
            return
        if not is_fin and not is_live:
            return
        parsed = parse_score_string(self._a_text)
        if not parsed:
            return
        teams = unquote((self._teams_buf or "").strip())
        # Teams often appear as "Home - Away " before the anchor
        if " - " not in teams:
            return
        # Prefer the last "Home - Away" segment if noise precedes
        parts = teams.split(" - ")
        if len(parts) < 2:
            return
        # Reconstruct: first segment may have leading junk from prior nodes;
        # take last two pieces joined as home/away if more than 2 splits
        if len(parts) == 2:
            home, away = parts[0].strip(), parts[1].strip()
        else:
            # e.g. "noise Home - Away" → home has noise; strip to last token group
            home, away = parts[-2].strip(), parts[-1].strip()
            # If home still has a newline prefix, take last line
            if "\n" in home:
                home = home.split("\n")[-1].strip()
        if not home or not away:
            return
        fthg, ftag = parsed
        self.rows.append(
            {
                "league": self.current_league,
                "home": home,
                "away": away,
                "fthg": fthg,
                "ftag": ftag,
                "is_live": is_live and not is_fin,
                "kickoff_hint": self._pending_time,
            }
        )
        self._capture_text_for_teams = False
        self._teams_buf = ""


def parse_score_data_html(raw_html: str, *, finished_only: bool = True) -> List[Dict[str, Any]]:
    """Parse #score-data inner HTML into score rows (MatchPredictor port)."""
    if not raw_html or not raw_html.strip():
        return []
    # Wrap like MatchPredictor: LoadHtml($\"<div>{rawHtml}</div>\")
    wrapped = f"<div>{raw_html}</div>"
    parser = _ScoreDataParser(finished_only=finished_only)
    parser.feed(wrapped)
    parser.close()
    return parser.rows


_MAN_EXPAND_NEXT = frozenset({"united", "utd", "city"})


def flashscore_url(day_offset: int = 0) -> str:
    """Build flashscore.mobi URL; prior days use ?d=-1, ?d=-2, …"""
    base = config.FLASHSCORE_URL.rstrip("/")
    offset = int(day_offset)
    if offset == 0:
        return base
    return f"{base}/?d={offset}"


def normalize_team_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"\([^)]*\)", " ", text)  # country tags e.g. (Kaz)
    for noise in (" fc", " afc", " sc", " cf", " fk"):
        if text.endswith(noise):
            text = text[: -len(noise)]
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    tokens = text.split()
    out: List[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok == "man" and nxt in _MAN_EXPAND_NEXT:
            out.append("manchester")
            i += 1
            continue
        if tok == "utd":
            out.append("united")
            i += 1
            continue
        out.append(tok)
        i += 1
    return " ".join(out)


def teams_match(a: str, b: str, *, min_score: Optional[int] = None) -> bool:
    """Fuzzy / containment team name match (MatchPredictor-inspired)."""
    from thefuzz import fuzz

    threshold = int(min_score if min_score is not None else config.FLASHSCORE_NAME_MATCH_MIN)
    na, nb = normalize_team_name(a), normalize_team_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na in nb or nb in na:
        return True
    score = max(
        fuzz.token_sort_ratio(na, nb),
        fuzz.token_set_ratio(na, nb),
        fuzz.partial_ratio(na, nb),
    )
    return score >= threshold


def _row_dedupe_key(row: Dict[str, Any]) -> Tuple[str, str, Any, Any, str]:
    return (
        normalize_team_name(row.get("home") or ""),
        normalize_team_name(row.get("away") or ""),
        row.get("fthg"),
        row.get("ftag"),
        (row.get("league") or "").strip().lower(),
    )


def dedupe_score_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep first occurrence of each finished score identity across day pages."""
    seen: Set[Tuple[str, str, Any, Any, str]] = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        key = _row_dedupe_key(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _fetch_score_data_html_on_page(page, day_offset: int, timeout_ms: int) -> str:
    url = flashscore_url(day_offset)
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    title = page.title()
    body = page.content()
    if looks_like_challenge_page(body, title):
        _record_cooldown()
        raise FlashscoreBlockedError(f"Blocked by challenge page: {title} ({url})")
    try:
        page.wait_for_selector("#score-data", timeout=timeout_ms)
    except Exception:
        body = page.content()
        title = page.title()
        if looks_like_challenge_page(body, title):
            _record_cooldown()
            raise FlashscoreBlockedError(f"Blocked while waiting for #score-data: {title}")
        raise FlashscoreUnavailableError(f"#score-data not found on {url}")
    return page.inner_html("#score-data") or ""


def fetch_score_data_html(day_offset: int = 0) -> str:
    """
    Open flashscore.mobi with Playwright (mobile UA), return #score-data innerHTML.
    day_offset: 0 = today, -1 = yesterday, etc.
    """
    _check_cooldown()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FlashscoreUnavailableError(
            "playwright is not installed — pip install playwright && playwright install chromium"
        ) from exc

    timeout_ms = int(config.FLASHSCORE_TIMEOUT_SEC * 1000)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=_MOBILE_UA,
                viewport={"width": 390, "height": 844},
                locale="en-US",
            )
            page = context.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            try:
                html = _fetch_score_data_html_on_page(page, day_offset, timeout_ms)
            finally:
                browser.close()
            return html
    except (FlashscoreBlockedError, FlashscoreCooldownError, FlashscoreUnavailableError):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Flashscore fetch failed: %s", exc)
        _record_cooldown()
        raise FlashscoreUnavailableError(str(exc)) from exc


def scrape_finished_scores(
    day_offsets: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch + parse finished (a.fin) scores from flashscore.mobi.
    Multiple day offsets share one browser session; rows are deduped.
    """
    offsets = day_offsets if day_offsets is not None else [0]
    # Stable unique order: today first, then older days
    unique_offsets: List[int] = []
    for off in offsets:
        o = int(off)
        if o not in unique_offsets:
            unique_offsets.append(o)
    if not unique_offsets:
        unique_offsets = [0]

    _check_cooldown()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FlashscoreUnavailableError(
            "playwright is not installed — pip install playwright && playwright install chromium"
        ) from exc

    timeout_ms = int(config.FLASHSCORE_TIMEOUT_SEC * 1000)
    merged: List[Dict[str, Any]] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=_MOBILE_UA,
                viewport={"width": 390, "height": 844},
                locale="en-US",
            )
            page = context.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            try:
                for off in unique_offsets:
                    html = _fetch_score_data_html_on_page(page, off, timeout_ms)
                    day_rows = parse_score_data_html(html, finished_only=True)
                    logger.info(
                        "Flashscore d=%s returned %d finished scores", off, len(day_rows)
                    )
                    merged.extend(day_rows)
            finally:
                browser.close()
    except (FlashscoreBlockedError, FlashscoreCooldownError, FlashscoreUnavailableError):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Flashscore multi-day scrape failed: %s", exc)
        _record_cooldown()
        raise FlashscoreUnavailableError(str(exc)) from exc

    rows = dedupe_score_rows(merged)
    logger.info(
        "Flashscore scrape returned %d finished scores (offsets=%s, raw=%d)",
        len(rows),
        unique_offsets,
        len(merged),
    )
    return rows
