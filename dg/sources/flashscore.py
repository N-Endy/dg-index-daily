"""Flashscore.mobi score scrape — ported from MatchPredictor WebScraperService."""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
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
    return text


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


def fetch_score_data_html() -> str:
    """
    Open flashscore.mobi with Playwright (mobile UA), return #score-data innerHTML.
    """
    _check_cooldown()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FlashscoreUnavailableError(
            "playwright is not installed — pip install playwright && playwright install chromium"
        ) from exc

    url = config.FLASHSCORE_URL
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
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            title = page.title()
            body = page.content()
            if looks_like_challenge_page(body, title):
                _record_cooldown()
                browser.close()
                raise FlashscoreBlockedError(f"Blocked by challenge page: {title}")
            try:
                page.wait_for_selector("#score-data", timeout=timeout_ms)
            except Exception:
                body = page.content()
                title = page.title()
                browser.close()
                if looks_like_challenge_page(body, title):
                    _record_cooldown()
                    raise FlashscoreBlockedError(f"Blocked while waiting for #score-data: {title}")
                raise FlashscoreUnavailableError("#score-data not found on flashscore.mobi")
            html = page.inner_html("#score-data")
            browser.close()
            return html or ""
    except (FlashscoreBlockedError, FlashscoreCooldownError, FlashscoreUnavailableError):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Flashscore fetch failed: %s", exc)
        _record_cooldown()
        raise FlashscoreUnavailableError(str(exc)) from exc


def scrape_finished_scores() -> List[Dict[str, Any]]:
    """Fetch + parse finished (a.fin) scores from flashscore.mobi."""
    html = fetch_score_data_html()
    rows = parse_score_data_html(html, finished_only=True)
    logger.info("Flashscore scrape returned %d finished scores", len(rows))
    return rows
