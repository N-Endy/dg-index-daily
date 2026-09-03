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
MATCH_ID_RE = re.compile(r"/match/([A-Za-z0-9]+)")
_MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)

_STATS_LABELS = {
    "Total shots": ("hs", "as_shots"),
    "Shots on target": ("hst", "ast"),
    "Corner kicks": ("hc", "ac"),
    "Yellow cards": ("hy", "ay"),
    "Red cards": ("hr", "ar"),
}
_STAT_LINE_RE = re.compile(
    r"^(\d+)\s+("
    + "|".join(re.escape(label) for label in _STATS_LABELS)
    + r")\s+(\d+)$"
)
# Encoded feed inside window.environment: SG÷Total shots¬SH÷12¬SI÷32
_FEED_STAT_RE = re.compile(r"SG÷([^¬]+)¬SH÷(\d+)¬SI÷(\d+)")
_BLOCK_TAGS = frozenset({"div", "tr", "p", "br", "li", "table", "section", "h3", "h4"})

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
        self._a_match_id: Optional[str] = None
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
            href = attrs_d.get("href", "")
            m = MATCH_ID_RE.search(href)
            self._a_match_id = m.group(1) if m else None
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
                "match_id": self._a_match_id,
            }
        )
        self._capture_text_for_teams = False
        self._teams_buf = ""
        self._a_match_id = None


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


def match_stats_url(match_id: str) -> str:
    base = config.FLASHSCORE_URL.rstrip("/")
    mid = (match_id or "").strip()
    return f"{base}/match/{mid}/?t=stats"


class _MatchStatsTextParser(HTMLParser):
    """Flatten a stats page into newline-separated text for line matching."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)


def _apply_stat_pair(out: Dict[str, int], label: str, home_v: int, away_v: int) -> None:
    keys = _STATS_LABELS.get(label)
    if not keys:
        return
    out[keys[0]] = home_v
    out[keys[1]] = away_v


def _parse_stats_feed(raw_html: str) -> Dict[str, int]:
    """Parse SG÷Label¬SH÷home¬SI÷away tokens embedded in page scripts."""
    out: Dict[str, int] = {}
    for m in _FEED_STAT_RE.finditer(raw_html or ""):
        label = (m.group(1) or "").strip()
        try:
            home_v = int(m.group(2))
            away_v = int(m.group(3))
        except (TypeError, ValueError):
            continue
        _apply_stat_pair(out, label, home_v, away_v)
    return out


def parse_match_stats_html(raw_html: str) -> Dict[str, int]:
    """
    Extract integer home/away stat pairs from a flashscore.mobi ?t=stats page.

    Supports:
    - flat lines like ``21 Total shots 10`` (legacy)
    - WCL three-line rows: home value / label / away value
    - SG÷…¬SH÷…¬SI÷… feed tokens when text scanning finds nothing
    """
    if not raw_html or not raw_html.strip():
        return {}
    parser = _MatchStatsTextParser()
    parser.feed(raw_html)
    parser.close()
    text = re.sub(r"[ \t]+", " ", "".join(parser.parts))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out: Dict[str, int] = {}

    for line in lines:
        m = _STAT_LINE_RE.match(line)
        if not m:
            continue
        _apply_stat_pair(out, m.group(2), int(m.group(1)), int(m.group(3)))

    i = 0
    while i < len(lines) - 2:
        home_s, label, away_s = lines[i], lines[i + 1], lines[i + 2]
        if home_s.isdigit() and away_s.isdigit() and label in _STATS_LABELS:
            _apply_stat_pair(out, label, int(home_s), int(away_s))
            i += 3
            continue
        i += 1

    if not out:
        out = _parse_stats_feed(raw_html)
    return out


def fetch_match_stats(match_ids: List[str]) -> Dict[str, Dict[str, int]]:
    """
    Fetch ?t=stats for each match id using one Chromium session.

    Missing or failed ids are omitted. Caps at FLASHSCORE_STATS_MAX_MATCHES
    and sleeps FLASHSCORE_STATS_DELAY_SEC between pages.
    """
    ids: List[str] = []
    seen: Set[str] = set()
    for mid in match_ids:
        m = (mid or "").strip()
        if not m or m in seen:
            continue
        seen.add(m)
        ids.append(m)
    max_n = max(0, int(config.FLASHSCORE_STATS_MAX_MATCHES))
    ids = ids[:max_n]
    if not ids:
        return {}

    _check_cooldown()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FlashscoreUnavailableError(
            "playwright is not installed — pip install playwright && playwright install chromium"
        ) from exc

    timeout_ms = int(config.FLASHSCORE_TIMEOUT_SEC * 1000)
    delay = max(0.0, float(config.FLASHSCORE_STATS_DELAY_SEC))
    out: Dict[str, Dict[str, int]] = {}
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
                for i, mid in enumerate(ids):
                    if i > 0 and delay > 0:
                        time.sleep(delay)
                    url = match_stats_url(mid)
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                        title = page.title()
                        body = page.content()
                        if looks_like_challenge_page(body, title):
                            _record_cooldown()
                            raise FlashscoreBlockedError(
                                f"Blocked by challenge page: {title} ({url})"
                            )
                        stats = parse_match_stats_html(body)
                        if stats:
                            out[mid] = stats
                        else:
                            logger.warning("Flashscore stats empty for match_id=%s", mid)
                    except FlashscoreBlockedError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Flashscore stats fetch failed for %s: %s", mid, exc
                        )
            finally:
                browser.close()
    except (FlashscoreBlockedError, FlashscoreCooldownError, FlashscoreUnavailableError):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Flashscore stats session failed: %s", exc)
        _record_cooldown()
        raise FlashscoreUnavailableError(str(exc)) from exc
    return out


_MAN_EXPAND_NEXT = frozenset({"united", "utd", "city"})

# Harmless longer-name suffixes (Derby ⊂ Derby County).
_TEAM_SUFFIX_NOISE = frozenset(
    {
        "county",
        "city",
        "town",
        "united",
        "athletic",
        "athl",
        "hotspur",
        "wanderers",
        "albion",
        "rovers",
        "football",
        "club",
        "cf",
        "fc",
        "afc",
        "sc",
    }
)


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


def _split_team_qualifiers(normalized: str) -> Tuple[frozenset, str]:
    """Return (qualifier frozenset, core name without qualifier tokens)."""
    tokens = [t for t in (normalized or "").split() if t]
    if not tokens:
        return frozenset(), ""
    always = frozenset(
        {
            "u16",
            "u17",
            "u18",
            "u19",
            "u20",
            "u21",
            "u22",
            "u23",
            "women",
            "womn",
            "reserves",
            "reserve",
            "res",
        }
    )
    trailing_only = frozenset({"w", "b", "ii"})
    quals: List[str] = []
    core: List[str] = []
    for i, tok in enumerate(tokens):
        is_last = i == len(tokens) - 1
        if tok in always or (tok in trailing_only and is_last and len(tokens) > 1):
            quals.append(tok)
        else:
            core.append(tok)
    return frozenset(quals), " ".join(core)


def _is_strict_token_prefix(short_tokens: List[str], long_tokens: List[str]) -> bool:
    """True when short is an exact leading token sequence of long (Rayo ⊂ Rayo Vallecano)."""
    if not short_tokens or len(short_tokens) > len(long_tokens):
        return False
    return long_tokens[: len(short_tokens)] == short_tokens


def teams_match(a: str, b: str, *, min_score: Optional[int] = None) -> bool:
    """Fuzzy team name match (qualifier-aware; no loose substring containment)."""
    threshold = int(min_score if min_score is not None else config.FLASHSCORE_NAME_MATCH_MIN)
    return team_match_score(a, b) >= threshold


def team_match_score(a: str, b: str) -> int:
    """0–100 similarity after normalize; qualifier mismatch → 0."""
    from thefuzz import fuzz

    na, nb = normalize_team_name(a), normalize_team_name(b)
    if not na or not nb:
        return 0
    qa, ca = _split_team_qualifiers(na)
    qb, cb = _split_team_qualifiers(nb)
    if qa != qb:
        return 0
    if not ca or not cb:
        return 0
    if ca == cb:
        return 100

    ta, tb = ca.split(), cb.split()
    # Flashscore shorthand: first-token prefix only (Rayo vs Rayo Vallecano, not Villa vs Aston Villa).
    if len(ta) != len(tb):
        short_t, long_t = (ta, tb) if len(ta) < len(tb) else (tb, ta)
        if _is_strict_token_prefix(short_t, long_t):
            return 100

    set_a, set_b = set(ta), set(tb)
    if set_a <= set_b or set_b <= set_a:
        short, long = (set_a, set_b) if len(set_a) <= len(set_b) else (set_b, set_a)
        extras = long - short
        if not extras or extras <= _TEAM_SUFFIX_NOISE:
            return 100
        # Incomplete short name (Villa ⊂ Aston Villa) — do not treat as exact
        return int(fuzz.token_sort_ratio(ca, cb))

    # Short single token vs multi-token: ignore partial/set dominance
    if (len(ta) == 1 and len(tb) > 1) or (len(tb) == 1 and len(ta) > 1):
        return int(fuzz.token_sort_ratio(ca, cb))

    return int(
        max(
            fuzz.token_sort_ratio(ca, cb),
            fuzz.token_set_ratio(ca, cb),
            fuzz.partial_ratio(ca, cb),
        )
    )


# League label noise (do not include "championship" — bare fixture names use it as the identity).
_LEAGUE_STOPWORDS = frozenset(
    {
        "standings",
        "qualification",
        "play",
        "offs",
        "round",
        "group",
        "stage",
        "phase",
        "preliminary",
        "league",
        "division",
        "cup",
        "of",
        "the",
        "and",
    }
)

_LEAGUE_ALIASES = {
    "laliga": ("la", "liga"),
    "ligue": ("ligue",),
}

_LEAGUE_COUNTRIES = frozenset(
    {
        "england",
        "scotland",
        "wales",
        "ireland",
        "spain",
        "italy",
        "germany",
        "france",
        "portugal",
        "netherlands",
        "belgium",
        "brazil",
        "argentina",
        "mexico",
        "usa",
        "turkey",
        "greece",
        "poland",
        "sweden",
        "norway",
        "denmark",
        "austria",
        "switzerland",
        "romania",
        "ukraine",
        "russia",
        "japan",
        "korea",
        "china",
        "australia",
        "canada",
        "uganda",
        "fiji",
        "kenya",
        "ghana",
        "nigeria",
        "egypt",
        "morocco",
        "algeria",
        "tunisia",
        "senegal",
        "cameroon",
        "chile",
        "colombia",
        "peru",
        "ecuador",
        "paraguay",
        "uruguay",
        "bolivia",
        "venezuela",
        "india",
        "indonesia",
        "thailand",
        "vietnam",
        "malaysia",
        "singapore",
        "saudi",
        "arabia",
        "qatar",
        "uae",
        "israel",
        "croatia",
        "serbia",
        "czech",
        "slovakia",
        "hungary",
        "finland",
        "iceland",
        "cyprus",
        "malta",
        "luxembourg",
        "georgia",
        "armenia",
        "azerbaijan",
        "kazakhstan",
        "uzbekistan",
        "iran",
        "iraq",
        "jordan",
        "lebanon",
        "syria",
        "yemen",
        "oman",
        "bahrain",
        "kuwait",
        "new",
        "zealand",
        "south",
        "africa",
        "zimbabwe",
        "zambia",
        "botswana",
        "namibia",
        "mozambique",
        "angola",
        "ethiopia",
        "sudan",
        "tanzania",
        "rwanda",
        "burundi",
        "congo",
        "ivory",
        "coast",
        "mali",
        "burkina",
        "faso",
    }
)

_LEAGUE_YOUTH = frozenset(
    {"u16", "u17", "u18", "u19", "u20", "u21", "u22", "u23", "youth", "junior", "juniors"}
)


def _league_tokens(league: str) -> set[str]:
    """Tokenize league labels; keep country tokens for conflict checks."""
    text = unicodedata.normalize("NFKD", league or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[-.:/,()]+", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return set()
    raw = [p for p in text.split() if p]
    expanded: List[str] = []
    for p in raw:
        alias = _LEAGUE_ALIASES.get(p)
        if alias:
            expanded.extend(alias)
        else:
            expanded.append(p)
    meaningful = [p for p in expanded if p not in _LEAGUE_STOPWORDS]
    if meaningful:
        return set(meaningful)
    return set(expanded)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def league_match_score(a: str, b: str) -> float:
    """
    0–1 league label similarity.
    Same competition with one-sided country (Premier League vs ENGLAND: Premier League) → high.
    Conflicting countries or youth mismatch → low.
    """
    words_a = _league_tokens(a)
    words_b = _league_tokens(b)
    if not words_a or not words_b:
        return 0.0
    if words_a == words_b:
        return 1.0

    youth_a = words_a & _LEAGUE_YOUTH
    youth_b = words_b & _LEAGUE_YOUTH
    if youth_a != youth_b:
        return 0.25

    countries_a = words_a & _LEAGUE_COUNTRIES
    countries_b = words_b & _LEAGUE_COUNTRIES
    if countries_a and countries_b and countries_a.isdisjoint(countries_b):
        return min(0.35, _jaccard(words_a, words_b))

    comp_a = words_a - _LEAGUE_COUNTRIES - _LEAGUE_YOUTH
    comp_b = words_b - _LEAGUE_COUNTRIES - _LEAGUE_YOUTH
    if not comp_a or not comp_b:
        return _jaccard(words_a, words_b)
    if comp_a == comp_b:
        # Bare "Premier League" vs "UGANDA: Premier League": competition matches but
        # Flashscore country is not a usual home for that API label.
        only_a = countries_a - countries_b
        only_b = countries_b - countries_a
        if comp_a == {"premier"}:
            allowed = frozenset({"england", "scotland"})
            foreign = (only_a | only_b) - allowed
            if foreign:
                return 0.35
        return 1.0
    # Soft prefix match for long competition tokens
    score = _jaccard(comp_a, comp_b)
    for token in list(comp_a):
        if token in comp_b or len(token) < 4:
            continue
        if any(
            lt.startswith(token) or token.startswith(lt)
            for lt in comp_b
            if len(lt) >= 4
        ):
            score = max(score, 0.85)
    return min(1.0, score)


def row_fingerprint(row: Dict[str, Any]) -> str:
    home, away, fthg, ftag, league = _row_dedupe_key(row)
    return f"{home}|{away}|{fthg}|{ftag}|{league}"


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
                    for row in day_rows:
                        row["day_offset"] = off
                        merged.append(row)
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
