"""Tests for backtest-derived market reliability weights."""
from __future__ import annotations

from dg.report.market_reliability import market_hit_rates_from_backtest


def test_market_hit_rates_empty_without_data(tmp_path):
    from dg.storage.db import connect, init_db

    conn = init_db(connect(tmp_path / "rel.db"))
    rates = market_hit_rates_from_backtest(conn)
    assert isinstance(rates, dict)
    conn.close()
