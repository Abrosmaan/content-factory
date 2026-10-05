"""Юнит-тесты analyst: recalc_weights, kill criterion."""

from __future__ import annotations

from src.workers import analyst


def test_recalc_weights_normalizes_to_mean_one():
    rows_by_format = {
        "short": [
            {"ctr": 5.0, "retention_pct": 50.0, "subs_gained": 20},
            {"ctr": 4.0, "retention_pct": 45.0, "subs_gained": 15},
        ],
        "long_gaming": [
            {"ctr": 2.0, "retention_pct": 30.0, "subs_gained": 5},
        ],
    }
    w = analyst.recalc_weights(rows_by_format)
    assert "short" in w and "long_gaming" in w
    # short — лучше, должен быть выше long_gaming
    assert w["short"] > w["long_gaming"]
    # среднее ~ 1.0
    avg = sum(w.values()) / len(w)
    assert abs(avg - 1.0) < 0.01


def test_recalc_weights_returns_unit_on_empty():
    rows = {"short": [], "long_gaming": []}
    w = analyst.recalc_weights(rows)
    assert w["short"] == 1.0 and w["long_gaming"] == 1.0


def test_kill_criterion_fires_when_all_three_under_thresholds():
    rows = [
        {"ctr": 1.5, "retention_pct": 25},
        {"ctr": 2.2, "retention_pct": 28},
        {"ctr": 2.4, "retention_pct": 30},
    ]
    assert analyst.check_kill_criterion(rows) is True


def test_kill_criterion_does_not_fire_when_any_good():
    rows = [
        {"ctr": 5.0, "retention_pct": 50},
        {"ctr": 2.0, "retention_pct": 30},
        {"ctr": 1.0, "retention_pct": 20},
    ]
    assert analyst.check_kill_criterion(rows) is False


def test_kill_criterion_not_enough_data():
    rows = [{"ctr": 1.0, "retention_pct": 10}]
    assert analyst.check_kill_criterion(rows) is False
