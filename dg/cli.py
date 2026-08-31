"""CLI entrypoints: run | ingest | backfill-results | backtest | report | doctor."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from dg import config
from dg.ai.vet_strongest import vet_strongest_for_day
from dg.ingest.fixture_scores import sync_fixture_scores
from dg.ingest.fixtures import ingest_fixtures
from dg.ingest.ratings import ingest_ratings, team_ids_for_snapshot
from dg.ingest.results import backfill_results
from dg.model.evaluate import evaluate_joined
from dg.model.rules import predict_upcoming
from dg.model.supervised import train_if_ready
from dg.quality.checks import QualityReport, run_quality_checks
from dg.quality.doctor import run_doctor
from dg.report.loaders import today_wat
from dg.report.render import render_report, write_report
from dg.sources import datagaffer as dg_src
from dg.storage.db import db_session, init_db, latest_snapshot, snapshot_exists

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    config.ensure_dirs()
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    handlers: List[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    log_path = config.LOGS_DIR / f"run_{today_wat()}.log"
    handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(level=level, format=fmt, handlers=handlers, force=True)


def _log_run(conn, started: str, status: str, exit_code: int, stages: Dict[str, Any], message: str = "") -> None:
    conn.execute(
        """
        INSERT INTO run_log (started_at, finished_at, status, exit_code, stages_json, message)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            started,
            datetime.now(timezone.utc).isoformat(),
            status,
            exit_code,
            json.dumps(stages),
            message,
        ),
    )


def cmd_doctor(args: argparse.Namespace) -> int:
    setup_logging(args.verbose)
    meta, _, _ = dg_src.fetch_meta()
    ratings = dg_src.fetch_ratings(archive=False)
    fixtures: List[Dict[str, Any]] = []
    for feed in dg_src.fetch_all_fixtures(archive=False):
        fixtures.extend(feed.data)
    report = run_doctor(meta.raw, ratings.data, fixtures)
    for e in report.errors:
        logger.error("DOCTOR: %s", e)
    for w in report.warnings:
        logger.warning("DOCTOR: %s", w)
    if report.ok:
        logger.info("Doctor OK — %d teams, %d fixtures", len(ratings.data), len(fixtures))
        return config.EXIT_OK
    return config.EXIT_CRITICAL


def cmd_ingest(args: argparse.Namespace) -> int:
    setup_logging(args.verbose)
    init_db()
    started = datetime.now(timezone.utc).isoformat()
    stages: Dict[str, Any] = {}
    with db_session() as conn:
        try:
            meta, meta_bytes, _ = dg_src.fetch_meta()
            dg_src.archive_bytes("dg_meta.json", meta_bytes)
            stages["meta"] = meta.generated_at

            if snapshot_exists(conn, meta.generated_at) and not args.force:
                snap = conn.execute(
                    "SELECT id, n_teams FROM dg_snapshot WHERE generated_at = ?",
                    (meta.generated_at,),
                ).fetchone()
                logger.info("Already have snapshot %s — skipping ratings", meta.generated_at)
                stages["ratings"] = "skipped"
                snapshot_id = int(snap["id"])
                inserted = False
            else:
                ratings = dg_src.fetch_ratings(archive=True)
                doctor = run_doctor(meta.raw, ratings.data)
                if not doctor.ok:
                    for e in doctor.errors:
                        logger.error("%s", e)
                    _log_run(conn, started, "critical", config.EXIT_CRITICAL, stages, "doctor failed")
                    return config.EXIT_CRITICAL
                snapshot_id, inserted = ingest_ratings(
                    conn,
                    ratings.data,
                    generated_at=meta.generated_at,
                    payload_sha256=ratings.sha256,
                    meta=meta.raw,
                )
                stages["ratings"] = {"inserted": inserted, "snapshot_id": snapshot_id, "n": len(ratings.data)}

            known = team_ids_for_snapshot(conn, snapshot_id)
            all_fx: List[Dict[str, Any]] = []
            for feed in dg_src.fetch_all_fixtures(archive=True):
                all_fx.extend(feed.data)
            # Deduplicate by fixture_id keeping last
            by_id = {int(f["fixture_id"]): f for f in all_fx if "fixture_id" in f}
            fixtures = list(by_id.values())
            from dg.quality.doctor import DoctorReport, check_fixtures_resolve

            fx_report = DoctorReport()
            check_fixtures_resolve(fixtures, known, fx_report)
            if not fx_report.ok:
                for e in fx_report.errors:
                    logger.error("%s", e)
                stages["fixtures"] = "contract_fail"
                _log_run(conn, started, "critical", config.EXIT_CRITICAL, stages, "fixture contract")
                return config.EXIT_CRITICAL

            n_up, n_proj, warns = ingest_fixtures(
                conn, fixtures, snapshot_id=snapshot_id, known_team_ids=known
            )
            stages["fixtures"] = {"upserted": n_up, "projections": n_proj, "warnings": len(warns)}
            _log_run(conn, started, "ok", config.EXIT_OK, stages)
            logger.info("Ingest complete snapshot_id=%s", snapshot_id)
            return config.EXIT_OK
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ingest failed: %s", exc)
            _log_run(conn, started, "critical", config.EXIT_CRITICAL, stages, str(exc))
            return config.EXIT_CRITICAL


def _window_iso(days_ahead: int = 3) -> tuple:
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
    end = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%dT23:59:59")
    return start, end + "Z" if not end.endswith("Z") else end


def cmd_run(args: argparse.Namespace) -> int:
    setup_logging(args.verbose)
    init_db()
    started = datetime.now(timezone.utc).isoformat()
    stages: Dict[str, Any] = {}
    exit_code = config.EXIT_OK
    status = "ok"
    message = ""

    with db_session() as conn:
        try:
            if args.dry_run:
                snap = latest_snapshot(conn)
                if snap is None:
                    logger.error("Dry-run requires an existing snapshot — run ingest first")
                    return config.EXIT_CRITICAL
                snapshot_id = int(snap["id"])
                generated_at = snap["generated_at"]
                n_teams = int(snap["n_teams"])
                quality = QualityReport()
                quality.warnings.append("dry-run: skipped fetch/ingest/quality writes")
                stages["dry_run"] = True
            else:
                meta, meta_bytes, _ = dg_src.fetch_meta()
                dg_src.archive_bytes("dg_meta.json", meta_bytes)
                generated_at = meta.generated_at
                stages["meta"] = generated_at

                if snapshot_exists(conn, generated_at):
                    snap = conn.execute(
                        "SELECT * FROM dg_snapshot WHERE generated_at = ?",
                        (generated_at,),
                    ).fetchone()
                    snapshot_id = int(snap["id"])
                    n_teams = int(snap["n_teams"])
                    stages["ratings"] = "already_present"
                    logger.info("Snapshot %s already present — refreshing fixtures only", generated_at)
                    ratings_data = None
                else:
                    ratings = dg_src.fetch_ratings(archive=True)
                    ratings_data = ratings.data
                    doctor = run_doctor(meta.raw, ratings_data)
                    if not doctor.ok:
                        for e in doctor.errors:
                            logger.error("%s", e)
                        _log_run(conn, started, "critical", config.EXIT_CRITICAL, stages, "doctor")
                        return config.EXIT_CRITICAL
                    snapshot_id, _ = ingest_ratings(
                        conn,
                        ratings_data,
                        generated_at=generated_at,
                        payload_sha256=ratings.sha256,
                        meta=meta.raw,
                    )
                    n_teams = len(ratings_data)
                    stages["ratings"] = {"snapshot_id": snapshot_id, "n": n_teams}

                known = team_ids_for_snapshot(conn, snapshot_id)
                all_fx: List[Dict[str, Any]] = []
                for feed in dg_src.fetch_all_fixtures(archive=True):
                    all_fx.extend(feed.data)
                by_id = {int(f["fixture_id"]): f for f in all_fx if "fixture_id" in f}
                fixtures = list(by_id.values())

                from dg.quality.doctor import DoctorReport, check_fixtures_resolve

                fx_report = DoctorReport()
                check_fixtures_resolve(fixtures, known, fx_report)
                if not fx_report.ok:
                    for e in fx_report.errors:
                        logger.error("%s", e)
                    _log_run(conn, started, "critical", config.EXIT_CRITICAL, stages, "fixture ids")
                    return config.EXIT_CRITICAL

                n_up, n_proj, warns = ingest_fixtures(
                    conn, fixtures, snapshot_id=snapshot_id, known_team_ids=known
                )
                stages["fixtures"] = {"upserted": n_up, "projections": n_proj}
                if warns:
                    exit_code = config.EXIT_PARTIAL
                    status = "partial"

                quality = run_quality_checks(conn, snapshot_id, generated_at)
                stages["quality"] = {
                    "new": len(quality.new_teams),
                    "missing": len(quality.missing_teams),
                    "anomalies": len(quality.anomalies),
                }

            date_from, date_to = _window_iso(3)
            # date_to used as exclusive-ish upper bound — fixtures store full ISO
            predictions = predict_upcoming(
                conn, snapshot_id, date_from=date_from[:10], date_to=None
            )
            # Filter to next ~3 days in Python for simplicity
            cutoff = datetime.now(timezone.utc) + timedelta(days=3)
            filtered = []
            for p in predictions:
                try:
                    dt = datetime.fromisoformat((p.get("date_utc") or "").replace("Z", "+00:00"))
                except ValueError:
                    filtered.append(p)
                    continue
                if dt <= cutoff + timedelta(days=1):
                    filtered.append(p)
            predictions = filtered
            stages["predictions"] = len(predictions)

            backtest = evaluate_joined(conn)
            stages["backtest_n"] = backtest.get("n", 0)
            train_if_ready(conn)

            md = render_report(
                generated_at=generated_at,
                snapshot_id=snapshot_id,
                n_teams=n_teams,
                quality=quality,
                predictions=predictions,
                backtest=backtest,
                run_status=status,
            )
            if args.dry_run:
                # Still write report in dry-run per plan
                path = write_report(md)
                logger.info("Dry-run report written to %s", path)
            else:
                path = write_report(md)
                logger.info("Report written to %s", path)
            stages["report"] = str(path)

            _log_run(conn, started, status, exit_code, stages, message)
            return exit_code
        except Exception as exc:  # noqa: BLE001
            logger.exception("Daily run failed: %s", exc)
            _log_run(conn, started, "critical", config.EXIT_CRITICAL, stages, str(exc))
            return config.EXIT_CRITICAL


def cmd_backfill(args: argparse.Namespace) -> int:
    setup_logging(args.verbose)
    init_db()
    with db_session() as conn:
        try:
            counts = backfill_results(conn, season=args.season, include_new=not args.no_new)
            logger.info("Backfill counts: %s", counts)
            return config.EXIT_OK
        except Exception as exc:  # noqa: BLE001
            logger.exception("Backfill failed: %s", exc)
            return config.EXIT_CRITICAL


def cmd_sync_scores(args: argparse.Namespace) -> int:
    setup_logging(args.verbose)
    init_db()
    with db_session() as conn:
        try:
            summary = sync_fixture_scores(conn)
            logger.info("Sync scores: %s", summary)
            flash = summary.get("flashscore") or {}
            if flash.get("skipped_blocked") or flash.get("skipped_unavailable"):
                return config.EXIT_PARTIAL
            if summary.get("written", 0) == 0 and flash.get("skipped_cooldown"):
                return config.EXIT_PARTIAL
            return config.EXIT_OK
        except Exception as exc:  # noqa: BLE001
            logger.exception("Sync scores failed: %s", exc)
            return config.EXIT_CRITICAL


def cmd_vet_ai_picks(args: argparse.Namespace) -> int:
    setup_logging(args.verbose)
    init_db()
    day = (args.day or "").strip() or today_wat()
    with db_session() as conn:
        try:
            summary = vet_strongest_for_day(conn, day=day)
            logger.info("Vet AI picks: %s", summary)
            if summary.get("skipped_no_key"):
                return config.EXIT_OK
            if summary.get("errors"):
                return config.EXIT_PARTIAL
            return config.EXIT_OK
        except Exception as exc:  # noqa: BLE001
            logger.exception("Vet AI picks failed: %s", exc)
            return config.EXIT_CRITICAL


def cmd_backtest(args: argparse.Namespace) -> int:
    setup_logging(args.verbose)
    init_db()
    with db_session() as conn:
        summary = evaluate_joined(conn)
        print(json.dumps(summary, indent=2))
        return config.EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    setup_logging(args.verbose)
    init_db()
    with db_session() as conn:
        snap = latest_snapshot(conn)
        if snap is None:
            logger.error("No snapshot")
            return config.EXIT_CRITICAL
        snapshot_id = int(snap["id"])
        quality = run_quality_checks(conn, snapshot_id, snap["generated_at"])
        predictions = predict_upcoming(conn, snapshot_id)
        backtest = evaluate_joined(conn)
        md = render_report(
            generated_at=snap["generated_at"],
            snapshot_id=snapshot_id,
            n_teams=int(snap["n_teams"]),
            quality=quality,
            predictions=predictions,
            backtest=backtest,
        )
        path = write_report(md)
        logger.info("Wrote %s", path)
        return config.EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dg", description="DataGaffer daily DG Index pipeline")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Full daily cycle")
    run_p.add_argument("--dry-run", action="store_true", help="Report from DB without fetching")
    run_p.set_defaults(func=cmd_run)

    ing = sub.add_parser("ingest", help="Fetch & store ratings + fixtures")
    ing.add_argument("--force", action="store_true")
    ing.set_defaults(func=cmd_ingest)

    bf = sub.add_parser("backfill-results", help="Pull football-data.co.uk results")
    bf.add_argument("--season", default=config.DEFAULT_FD_SEASON)
    bf.add_argument("--no-new", action="store_true", help="Skip /new/ country CSVs")
    bf.set_defaults(func=cmd_backfill)

    ss = sub.add_parser(
        "sync-scores",
        help="Pull finished FT scores (Flashscore.mobi; optional API-Football leftovers)",
    )
    ss.set_defaults(func=cmd_sync_scores)

    vet = sub.add_parser(
        "vet-ai-picks",
        help="LLM-screen Strongest leans and persist AI Picks for the day",
    )
    vet.add_argument("--day", default=None, help="UTC date YYYY-MM-DD (default: today)")
    vet.set_defaults(func=cmd_vet_ai_picks)

    bt = sub.add_parser("backtest", help="Score predictions vs results")
    bt.set_defaults(func=cmd_backtest)

    rp = sub.add_parser("report", help="Regenerate report from DB")
    rp.set_defaults(func=cmd_report)

    doc = sub.add_parser("doctor", help="Live feed contract checks")
    doc.set_defaults(func=cmd_doctor)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
