"""Tests for agreement wording and chip labels."""
from __future__ import annotations

from dg.web.plain_language import agreement_hint, market_chip_label


def test_agreement_model_and_book():
    hint = agreement_hint("Over", "Over", "Over")
    assert hint["key"] == "aligned"
    assert hint["label"] == "Model + book agree"
    assert hint["sources"] == ["dg", "book"]
    assert hint["n_sources"] == 2


def test_agreement_dg_model_only():
    hint = agreement_hint("Over", "Over", None)
    assert hint["key"] == "aligned"
    assert hint["label"] == "DG model only"
    assert hint["sources"] == ["dg"]
    assert hint["n_sources"] == 1


def test_agreement_no_market_compare():
    hint = agreement_hint("Over", None, None)
    assert hint["key"] == "unknown"
    assert hint["label"] == "No market compare"
    assert hint["n_sources"] == 0


def test_market_chip_label_with_line():
    assert market_chip_label("corners_9_5", line=10.5) == "Corners 10.5"
    assert market_chip_label("goals_2_5", line=2.5) == "Goals 2.5"
