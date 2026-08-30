"""FastAPI web UI for the DG Index daily dashboard."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import markdown  # type: ignore[import-untyped]
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dg.report.ai_picks import load_ai_picks_page
from dg.report.best_leans import load_strongest_day
from dg.report.loaders import (
    enrich_prediction_for_display,
    group_predictions_by_date,
    load_dashboard_context,
    parse_market_filters,
)

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))
GUIDE_MD = WEB_DIR / "content" / "guide.md"

app = FastAPI(title="DG Index Daily", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


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

    ctx = load_dashboard_context(
        date_filter=date,
        league_filter=league,
        market_filters=market_filters,
        match_mode=mode or "all",
        min_prob=parsed_min_prob,
        min_conf=min_conf,
    )
    enriched = [enrich_prediction_for_display(p) for p in ctx["predictions"]]
    grouped = group_predictions_by_date(enriched)
    return TEMPLATES.TemplateResponse(
        request,
        "dashboard.html",
        {
            **ctx,
            "grouped": grouped,
            "n_shown": len(enriched),
        },
    )


@app.get("/strongest", response_class=HTMLResponse)
def strongest(request: Request):
    ctx = load_strongest_day()
    return TEMPLATES.TemplateResponse(
        request,
        "strongest.html",
        ctx,
    )


@app.get("/ai-picks", response_class=HTMLResponse)
def ai_picks(request: Request):
    ctx = load_ai_picks_page()
    return TEMPLATES.TemplateResponse(
        request,
        "ai_picks.html",
        ctx,
    )


@app.get("/guide", response_class=HTMLResponse)
def guide(request: Request):
    raw = GUIDE_MD.read_text(encoding="utf-8") if GUIDE_MD.exists() else "# Guide missing"
    html = markdown.markdown(raw, extensions=["tables", "fenced_code"])
    return TEMPLATES.TemplateResponse(
        request,
        "guide.html",
        {"guide_html": html},
    )
