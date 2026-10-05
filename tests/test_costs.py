"""Юнит-тесты core/costs: цены, форматтер /costs, dedup дневного алерта."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from src.core import costs
from src.workers import watchdog


# ---------- pricing math ----------

def test_anthropic_pricing():
    """1M input + 1M output должно дать $3 + $15 = $18."""
    captured = {}

    def fake_record(service, amt, **kw):
        captured["service"] = service
        captured["amount"] = amt
        captured["kw"] = kw

    with patch.object(costs, "record", side_effect=fake_record):
        costs.record_anthropic(1_000_000, 1_000_000)
    assert captured["service"] == "anthropic"
    assert abs(captured["amount"] - 18.0) < 1e-6


def test_creatomate_pricing():
    captured = {}
    with patch.object(costs, "record",
                      side_effect=lambda s, a, **kw: captured.update(
                          service=s, amount=a, kw=kw)):
        costs.record_creatomate(60.0)  # 60 sec * $0.03 = $1.80
    assert abs(captured["amount"] - 1.80) < 1e-6


def test_record_skips_zero():
    """amount_usd <= 0 → не пишем в БД."""
    with patch.object(costs, "connection") as conn_mock:
        costs.record("anthropic", 0.0)
        costs.record("anthropic", -1.0)
        conn_mock.assert_not_called()


# ---------- format_summary ----------

def test_format_summary_includes_all_services():
    seven = {
        "anthropic": {"amount_usd": 1.23, "quantity": 100_000, "n_calls": 5},
        "fal_kling": {"amount_usd": 0.60, "quantity": 10, "n_calls": 2},
    }
    text = costs.format_summary(seven, total_7d=1.83, total_30d=4.50,
                                total_all=15.20)
    assert "💰 Cost Dashboard — last 7 days" in text
    assert "Anthropic Claude: $1.23 (100000 tokens)" in text
    assert "fal.ai (footage): $0.60 (10 clips)" in text
    # сервис без записей всё равно показывается с $0.00
    assert "ElevenLabs TTS: $0.00 (0 chars)" in text
    assert "Total: $1.83" in text
    assert "За 30 дней: $4.50" in text
    assert "За всё время: $15.20" in text


# ---------- watchdog daily threshold ----------

def test_daily_threshold_alerts_once_per_day():
    """Если spent_today >= $5, send_alert вызывается ровно раз за сутки."""
    fake_state = {}

    def fake_get(key, default=None):
        return fake_state.get(key, default)

    def fake_set(key, value):
        fake_state[key] = value

    alerts = []

    with patch.object(watchdog, "check_stuck_records", return_value=[]), \
            patch.object(watchdog, "check_unresolved_errors", return_value=0), \
            patch.object(watchdog, "check_links_due", return_value=False), \
            patch.object(watchdog, "ping_healthchecks", return_value=True), \
            patch.object(watchdog, "check_owner_silence", return_value=None), \
            patch.object(watchdog.costs, "daily_total", return_value=7.50), \
            patch.object(watchdog.settings_store, "get",
                         side_effect=fake_get), \
            patch.object(watchdog.settings_store, "set_value",
                         side_effect=fake_set), \
            patch.object(watchdog, "send_alert",
                         side_effect=lambda m: alerts.append(m)):
        watchdog.run_once()
        watchdog.run_once()  # сразу второй прогон — алерта быть не должно

    assert len(alerts) == 1
    assert "Daily spend $7.50" in alerts[0]
    assert fake_state[costs.DAILY_ALERT_KEY] == (
        dt.datetime.now(dt.timezone.utc).date().isoformat()
    )


def test_daily_threshold_no_alert_below_limit():
    fake_state = {}
    alerts = []
    with patch.object(watchdog, "check_stuck_records", return_value=[]), \
            patch.object(watchdog, "check_unresolved_errors", return_value=0), \
            patch.object(watchdog, "check_links_due", return_value=False), \
            patch.object(watchdog, "ping_healthchecks", return_value=True), \
            patch.object(watchdog, "check_owner_silence", return_value=None), \
            patch.object(watchdog.costs, "daily_total", return_value=2.99), \
            patch.object(watchdog.settings_store, "get",
                         side_effect=lambda k, d=None: fake_state.get(k, d)), \
            patch.object(watchdog.settings_store, "set_value",
                         side_effect=lambda k, v: fake_state.__setitem__(k, v)), \
            patch.object(watchdog, "send_alert",
                         side_effect=lambda m: alerts.append(m)):
        watchdog.run_once()
    assert alerts == []
    assert costs.DAILY_ALERT_KEY not in fake_state
