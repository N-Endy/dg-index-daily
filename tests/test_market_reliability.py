"""Tests for backtest-derived market reliability weights."""
from __future__ import annotations

from dg.report.best_leans import (
    clear_market_hit_rates_cache,
    get_market_hit_rates,
    seed_market_hit_rates_cache,
)
from dg.report.market_reliability import (
    hit_rates_from_evaluate_summary,
    market_hit_rates_from_backtest,
)


def test_market_hit_rates_empty_without_data(tmp_path):
    from dg.storage.db import connect, init_db

    conn = init_db(connect(tmp_path / "rel.db"))
    rates = market_hit_rates_from_backtest(conn)
    assert isinstance(rates, dict)
    conn.close()


def test_hit_rates_from_evaluate_summary_and_seed():
    summary = {
        "markets": {
            "goals_2_5": {"rule": {"hit_rate": 0.62, "n_graded": 120}},
            "btts": {"rule": {"hit_rate": 0.55, "n_graded": 5}},
        }
    }
    rates = hit_rates_from_evaluate_summary(summary)
    assert rates.get("goals_2_5") == 0.62
    # below STRONGEST_MARKET_HIT_MIN_GRADED → omitted
    assert "btts" not in rates

    clear_market_hit_rates_cache()
    seed_market_hit_rates_cache(rates)
    # get_market_hit_rates must not re-run evaluate when seeded
    assert get_market_hit_rates(conn=None) == rates  # type: ignore[arg-type]
    clear_market_hit_rates_cache()
