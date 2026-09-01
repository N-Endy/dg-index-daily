"""Tests for Dixon-Coles score matrix."""
from __future__ import annotations

from dg.model.goals import score_matrix


def test_dixon_coles_off_matches_independent():
    base = score_matrix(1.2, 1.0, max_goals=5, rho=0.0)
    plain = score_matrix(1.2, 1.0, max_goals=5)
    for i in range(6):
        for j in range(6):
            assert base[i][j] == plain[i][j]


def test_dixon_coles_on_still_normalises():
    mat = score_matrix(1.2, 1.0, max_goals=6, rho=-0.05)
    total = sum(mat[i][j] for i in range(7) for j in range(7))
    assert abs(total - 1.0) < 1e-9


def test_dixon_coles_on_differs_from_independent():
    plain = score_matrix(1.1, 1.0, max_goals=6, rho=0.0)
    dc = score_matrix(1.1, 1.0, max_goals=6, rho=-0.08)
    assert any(
        plain[i][j] != dc[i][j]
        for i in range(7)
        for j in range(7)
    )
