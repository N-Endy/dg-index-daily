"""Daily markdown report renderer."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dg import config
from dg.model.registry import model_version
from dg.quality.checks import QualityReport


def render_report(
    *,
    generated_at: str,
    snapshot_id: int,
    n_teams: int,
    quality: QualityReport,
    predictions: List[Dict[str, Any]],
    doctor_warnings: Optional[List[str]] = None,
    backtest: Optional[Dict[str, Any]] = None,
    run_status: str = "ok",
) -> str:
    day = datetime.now(timezone.utc).date().isoformat()
    mv = model_version()
    lines: List[str] = []
    lines.append(f"# DG Index Daily Report — {day}")
    lines.append("")
    lines.append("## Scraping & Data Quality")
    lines.append("")
    lines.append(f"- Run status: **{run_status}**")
    lines.append(f"- DG `generated_at`: `{generated_at}`")
    if quality.staleness_hours is not None and quality.staleness_hours == quality.staleness_hours:
        lines.append(f"- Data age: **{quality.staleness_hours:.1f} hours**")
    lines.append(f"- Snapshot id: `{snapshot_id}` — teams: **{n_teams}**")
    lines.append(
        f"- New teams: {', '.join(quality.new_teams) if quality.new_teams else '_none_'}"
    )
    lines.append(
        f"- Missing vs prior: {', '.join(quality.missing_teams) if quality.missing_teams else '_none_'}"
    )
    if quality.anomalies:
        lines.append(f"- Anomalies flagged: **{len(quality.anomalies)}**")
        for a in quality.anomalies[:15]:
            lines.append(
                f"  - {a['team']} `{a['metric']}` Δ={a['delta']} "
                f"(threshold ±{a['threshold']})"
            )
        if len(quality.anomalies) > 15:
            lines.append(f"  - … and {len(quality.anomalies) - 15} more")
    else:
        lines.append("- Anomalies flagged: _none_")
    if doctor_warnings:
        lines.append("- Doctor warnings:")
        for w in doctor_warnings:
            lines.append(f"  - {w}")
    for w in quality.warnings:
        if w not in (doctor_warnings or []):
            lines.append(f"- Note: {w}")

    lines.append("")
    lines.append("## Upcoming Fixtures with Predictions")
    lines.append("")
    if not predictions:
        lines.append("_No upcoming fixtures matched._")
    else:
        # Group by date (UTC day)
        by_day: Dict[str, List[Dict[str, Any]]] = {}
        for p in predictions:
            d = (p.get("date_utc") or "")[:10] or "unknown"
            by_day.setdefault(d, []).append(p)
        for d in sorted(by_day):
            lines.append(f"### {d}")
            lines.append("")
            lines.append(
                "| League | Home | Away | Lean | Conf | Character | DG sim | Book | Key drivers |"
            )
            lines.append(
                "|--------|------|------|------|------|-----------|--------|------|-------------|"
            )
            for p in by_day[d]:
                drivers = "; ".join(p.get("drivers") or [])[:80]
                probs = p.get("probs") or {}
                if isinstance(probs, str):
                    try:
                        probs = json.loads(probs)
                    except (json.JSONDecodeError, TypeError):
                        probs = {}
                lean = p.get("lean")
                pct = ""
                if lean == "Home" and probs.get("home") is not None:
                    pct = f" ({int(round(float(probs['home']) * 100))}%)"
                elif lean == "Away" and probs.get("away") is not None:
                    pct = f" ({int(round(float(probs['away']) * 100))}%)"
                elif lean == "Draw" and probs.get("draw") is not None:
                    pct = f" ({int(round(float(probs['draw']) * 100))}%)"
                lines.append(
                    f"| {p.get('league') or ''} | {p.get('home_name')} | {p.get('away_name')} "
                    f"| **{lean}**{pct} | {p.get('confidence')} | {p.get('match_character')} "
                    f"| {p.get('dg_sim_lean') or '—'} | {p.get('book_lean') or '—'} | {drivers} |"
                )
                if probs.get("dgrtg_home") is not None and probs.get("dgrtg_away") is not None:
                    lines.append(
                        f"  - Strength: {p.get('home_name')} {float(probs['dgrtg_home']):.2f} vs "
                        f"{p.get('away_name')} {float(probs['dgrtg_away']):.2f}"
                    )
                markets = p.get("markets") or {}
                if isinstance(markets, dict):
                    bits = []
                    for mk, mv in markets.items():
                        if mk == "version" or not isinstance(mv, dict):
                            continue
                        prob_s = ""
                        if mv.get("prob") is not None:
                            try:
                                prob_s = f" {int(round(float(mv['prob']) * 100))}%"
                            except (TypeError, ValueError):
                                prob_s = ""
                        bits.append(
                            f"{mv.get('label', mk)}: {mv.get('lean')}{prob_s} ({mv.get('confidence')})"
                        )
                    if bits:
                        lines.append(f"  - Markets: {'; '.join(bits)}")
            lines.append("")

    lines.append("## Model Notes")
    lines.append("")
    lines.append(f"- Model version: `{mv}`")
    lines.append(
        "- Predictions are **directional / exploratory** — rule-based composite, "
        "**not** a trained supervised model."
    )
    lines.append(
        "- DG Index is one input among many; sports outcomes are volatile."
    )
    if backtest:
        lines.append(f"- Backtest joined rows: **{backtest.get('n', 0)}**")
        models = backtest.get("models") or {}
        for name, m in models.items():
            if m.get("n"):
                brier = m.get("brier")
                ll = m.get("logloss")
                brier_s = f"{brier:.4f}" if brier is not None else "n/a"
                ll_s = f"{ll:.4f}" if ll is not None else "n/a"
                lines.append(
                    f"  - `{name}`: Brier={brier_s}, logloss={ll_s} (n={m['n']})"
                )
        if backtest.get("n", 0) < 50:
            lines.append(
                "- Insufficient joined outcomes for strong claims — treat leans as exploratory."
            )
        mkt = backtest.get("markets") or {}
        if mkt:
            lines.append("- Per-market Brier (where labels exist):")
            for mk, srcs in sorted(mkt.items()):
                parts = []
                for src, stats in srcs.items():
                    b = stats.get("brier")
                    bn = stats.get("n", 0)
                    if b is not None and bn:
                        parts.append(f"{src}={b:.4f} (n={bn})")
                if parts:
                    lines.append(f"  - `{mk}`: {', '.join(parts)}")
    lines.append("")
    return "\n".join(lines)


def write_report(markdown: str, day: Optional[str] = None) -> Path:
    config.ensure_dirs()
    day = day or datetime.now(timezone.utc).date().isoformat()
    path = config.REPORTS_DIR / f"report_{day}.md"
    path.write_text(markdown, encoding="utf-8")
    return path
