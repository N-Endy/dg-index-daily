"""Plain-language rewrites for dashboard labels."""
from __future__ import annotations

from typing import Optional


def lean_plain(lean: Optional[str]) -> str:
    mapping = {
        "Home": "Favours Home",
        "Away": "Favours Away",
        "Draw": "Favours Draw",
    }
    return mapping.get(lean or "", "No clear lean")


def side_plain(side: Optional[str], *, prefix: str) -> str:
    if not side:
        return f"{prefix}: —"
    label = {"Home": "home", "Away": "away", "Draw": "a draw"}.get(side, side.lower())
    if side == "Draw":
        return f"{prefix} a draw"
    return f"{prefix} {label}"


def confidence_blurb(confidence: Optional[str]) -> str:
    mapping = {
        "high": "High — several signals point the same way",
        "medium": "Medium — a moderate edge, not a lock",
        "low": "Low — signals are mixed or thin; treat lightly",
    }
    return mapping.get((confidence or "").lower(), "Confidence unknown")


def match_style_plain(character: Optional[str]) -> str:
    c = (character or "").lower()
    if "volatile" in c and "tight" in c:
        return "Could be tense but chaotic — stoppages, cards, or swings"
    if "volatile" in c:
        return "Likely open and chaotic — more transitions and chances"
    if "open" in c:
        return "Likely open — more attacking, higher chance of goals"
    if "tight" in c:
        return "Likely tight — fewer chances, more control"
    if "balanced" in c:
        return "Balanced tempo — neither side clearly forces an open game"
    return character or "Style unclear"


_DRIVER_MAP = (
    ("rating gap", "One side has a clear DG Rating strength advantage"),
    ("pressing mismatch", "One side presses much more aggressively than the other"),
    ("pace clash", "Combined attacking tempo is unusually high or low"),
    ("attack vs control home", "Home creates more scoring pressure than away can control"),
    ("attack vs control away", "Away creates more scoring pressure than home can control"),
    ("aggression asymmetry", "One side plays with much higher early-game intensity"),
    ("efficiency edge", "Home attack looks more efficient than away defence (or vice versa)"),
    ("form trend", "Recent DG Index trend favours one side"),
)


def driver_plain(raw: str) -> str:
    """Turn 'pressing mismatch (+0.20)' into a lay sentence, keep the score."""
    lower = raw.lower()
    explanation = None
    for key, text in _DRIVER_MAP:
        if key in lower:
            explanation = text
            break
    # Keep trailing parenthetical contribution if present
    score_bit = ""
    if "(" in raw and ")" in raw:
        score_bit = " " + raw[raw.rfind("(") : raw.rfind(")") + 1]
    if explanation:
        return explanation + score_bit
    return raw


def agreement_hint(
    lean: Optional[str],
    dg_sim: Optional[str],
    book: Optional[str],
) -> dict:
    """
    Compare our lean to DG sim and book.
    Returns {key, label} where key is aligned | partial | split | unknown.
    """
    ours = (lean or "").strip()
    if not ours:
        return {"key": "unknown", "label": "No lean"}
    matches = []
    if dg_sim:
        matches.append(dg_sim == ours)
    if book:
        matches.append(book == ours)
    if not matches:
        return {"key": "unknown", "label": "No market compare"}
    if all(matches):
        return {"key": "aligned", "label": "Aligned"}
    if any(matches):
        return {"key": "partial", "label": "Partial"}
    return {"key": "split", "label": "Split"}


_CHIP_LABELS = {
    "goals_2_5": "Goals 2.5",
    "goals_3_5": "Goals 3.5",
    "btts": "BTTS",
    "team_goals_home_1_5": "Home 1.5",
    "team_goals_away_1_5": "Away 1.5",
    "fh_1x2": "FH 1X2",
    "fh_over_0_5": "FH 0.5",
    "corners_9_5": "Corners",
    "shots_25_5": "Shots",
    "sot_8_5": "SOT",
    "cards_3_5": "Cards",
}


def market_chip_label(key: Optional[str], fallback: Optional[str] = None) -> str:
    if key and key in _CHIP_LABELS:
        return _CHIP_LABELS[key]
    return fallback or (key or "Market")


def market_lean_plain(lean: Optional[str], key: Optional[str] = None) -> str:
    """Short display lean for market chips."""
    if not lean:
        return "—"
    if key == "fh_1x2":
        return {"Home": "FH Home", "Away": "FH Away", "Draw": "FH Draw"}.get(lean, lean)
    if lean in ("Over", "Under", "Yes", "No"):
        return lean
    return lean


def probability_plain(prob: Optional[float]) -> str:
    """Format a probability as a percentage string."""
    if prob is None:
        return ""
    try:
        p = float(prob)
    except (TypeError, ValueError):
        return ""
    if p <= 0 or p > 1.5:
        # Allow 0–1 or already-percent values
        if 1.5 < p <= 100:
            return f"{int(round(p))}%"
        return ""
    return f"{int(round(p * 100))}%"


def strength_gap_plain(
    home_name: Optional[str],
    away_name: Optional[str],
    dgrtg_home: Optional[float],
    dgrtg_away: Optional[float],
    rating_gap: Optional[float] = None,
) -> Optional[str]:
    """
    One-line strength summary, e.g.
    'Arsenal 2.37 vs Brighton 2.00 — clear home edge'
    """
    if dgrtg_home is None or dgrtg_away is None:
        return None
    try:
        h, a = float(dgrtg_home), float(dgrtg_away)
    except (TypeError, ValueError):
        return None
    gap = float(rating_gap) if rating_gap is not None else (h - a)
    abs_g = abs(gap)
    if abs_g < 0.08:
        edge = "evenly matched"
    elif abs_g < 0.20:
        edge = "slight home edge" if gap > 0 else "slight away edge"
    elif abs_g < 0.40:
        edge = "clear home edge" if gap > 0 else "clear away edge"
    else:
        edge = "strong home edge" if gap > 0 else "strong away edge"
    hn = home_name or "Home"
    an = away_name or "Away"
    return f"{hn} {h:.2f} vs {an} {a:.2f} — {edge}"
