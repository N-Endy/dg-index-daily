"""FastAPI web UI for the DG Index daily dashboard."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import markdown  # type: ignore[import-untyped]
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from dg import config
from dg.report.ai_picks import load_ai_picks_page
from dg.report.best_leans import load_strongest_day
from dg.report.loaders import (
    enrich_prediction_for_display,
    group_predictions_by_date,
    load_dashboard_context,
    parse_market_filters,
    today_wat,
)
from dg.report.score_hints import apply_score_hints_to_predictions, confirm_score_link
from dg.report.status import load_status_context
from dg.storage.db import db_session

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))
GUIDE_MD = WEB_DIR / "content" / "guide.md"

app = FastAPI(title="DG Index Daily", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


def _score_link_authorized(request: Request) -> bool:
    secret = config.SCORE_LINK_SECRET
    if not secret:
        return False
    header = (request.headers.get("X-Score-Link-Secret") or "").strip()
    if header and header == secret:
        return True
    cookie = (request.cookies.get(config.SCORE_LINK_COOKIE) or "").strip()
    return bool(cookie and cookie == secret)


class ScoreConfirmBody(BaseModel):
    fixture_id: int = Field(..., ge=1)
    flashscore_row_id: int = Field(..., ge=1)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    league: Optional[str] = Query(None),
    m: Optional[List[str]] = Query(None, description="Market filters key:side"),
    mode: Optional[str] = Query("all", description="all | any"),
    min_prob: Optional[str] = Query(None, description="Minimum lean probability 0–1"),
    min_conf: Optional[str] = Query(None, description="low | medium | high"),
):
    market_filters = parse_market_filters(m or [])

    # HTML forms submit empty strings for "Any" selects; treat as unset
    raw_date = date
    if date is None:
        date = today_wat()
    elif (date or "").strip().lower() == "all":
        date = None
    else:
        date = (date or "").strip() or None
    league = (league or "").strip() or None
    min_conf = (min_conf or "").strip() or None

    parsed_min_prob: Optional[float] = None
    raw_prob = (min_prob or "").strip()
    if raw_prob:
        try:
            parsed_min_prob = float(raw_prob)
        except ValueError:
            parsed_min_prob = None
    if parsed_min_prob is not None:
        if parsed_min_prob <= 0 or parsed_min_prob > 1:
            parsed_min_prob = None
        else:
            steps = (0.55, 0.60, 0.65, 0.70)
            parsed_min_prob = min(steps, key=lambda s: abs(s - parsed_min_prob))

    today = today_wat()
    has_active_filters = bool(
        league
        or market_filters
        or parsed_min_prob
        or min_conf
        or (raw_date is not None and (raw_date or "").strip().lower() == "all")
        or (
            raw_date is not None
            and (raw_date or "").strip()
            and (raw_date or "").strip().lower() != "all"
            and (raw_date or "").strip() != today
        )
    )

    ctx = load_dashboard_context(
        date_filter=date,
        league_filter=league,
        market_filters=market_filters,
        match_mode=mode or "all",
        min_prob=parsed_min_prob,
        min_conf=min_conf,
    )
    enriched = [enrich_prediction_for_display(p) for p in ctx["predictions"]]
    apply_score_hints_to_predictions(enriched)
    grouped = group_predictions_by_date(enriched)
    return TEMPLATES.TemplateResponse(
        request,
        "dashboard.html",
        {
            **ctx,
            "grouped": grouped,
            "n_shown": len(enriched),
            "has_active_filters": has_active_filters,
            "score_link_unlocked": _score_link_authorized(request),
        },
    )


@app.get("/strongest", response_class=HTMLResponse)
def strongest(request: Request):
    ctx = load_strongest_day()
    picks = list(ctx.get("picks") or [])
    apply_score_hints_to_predictions(picks)
    ctx = {**ctx, "picks": picks, "score_link_unlocked": _score_link_authorized(request)}
    return TEMPLATES.TemplateResponse(
        request,
        "strongest.html",
        ctx,
    )


@app.get("/ai-picks", response_class=HTMLResponse)
def ai_picks(request: Request):
    ctx = load_ai_picks_page()
    picks = list(ctx.get("picks") or [])
    apply_score_hints_to_predictions(picks)
    ctx = {**ctx, "picks": picks, "score_link_unlocked": _score_link_authorized(request)}
    return TEMPLATES.TemplateResponse(
        request,
        "ai_picks.html",
        ctx,
    )


@app.get("/score-link/unlock")
def score_link_unlock(token: str = Query("")):
    secret = config.SCORE_LINK_SECRET
    if not secret or token.strip() != secret:
        return HTMLResponse(
            "<p>Invalid or missing SCORE_LINK_SECRET unlock token.</p>",
            status_code=403,
        )
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(
        key=config.SCORE_LINK_COOKIE,
        value=secret,
        httponly=True,
        samesite="strict",
        max_age=60 * 60 * 12,
    )
    return resp


@app.post("/api/score-link/confirm")
def score_link_confirm(request: Request, body: ScoreConfirmBody):
    if not config.SCORE_LINK_SECRET:
        return JSONResponse(
            {
                "ok": False,
                "error": "SCORE_LINK_SECRET is not set — cannot confirm score links",
            },
            status_code=403,
        )
    if not _score_link_authorized(request):
        return JSONResponse(
            {
                "ok": False,
                "error": "Unauthorized — open /score-link/unlock?token=… first",
            },
            status_code=403,
        )
    try:
        with db_session() as conn:
            result = confirm_score_link(conn, body.fixture_id, body.flashscore_row_id)
        return {"ok": True, **result}
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.get("/status", response_class=HTMLResponse)
def status_page(request: Request):
    ctx = load_status_context()
    return TEMPLATES.TemplateResponse(request, "status.html", ctx)


@app.get("/guide", response_class=HTMLResponse)
def guide(request: Request):
    raw = GUIDE_MD.read_text(encoding="utf-8") if GUIDE_MD.exists() else "# Guide missing"
    html = markdown.markdown(raw, extensions=["tables", "fenced_code"])
    return TEMPLATES.TemplateResponse(
        request,
        "guide.html",
        {"guide_html": html},
    )
