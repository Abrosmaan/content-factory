"""Юнит-тесты watchdog: silence escalation thresholds."""

from __future__ import annotations

import datetime as dt

import pytest

from src.core import settings_store
from src.workers import watchdog


@pytest.mark.parametrize("days_ago,expected", [
    (0,  None),
    (5,  None),
    (13, None),
    (14, "warning"),
    (20, "warning"),
    (21, "standby"),
    (40, "standby"),
])
def test_silence_thresholds(monkeypatch, days_ago, expected):
    now = dt.datetime.now(dt.timezone.utc)
    ts = (now - dt.timedelta(days=days_ago)).isoformat()
    monkeypatch.setattr(settings_store, "get",
                        lambda key, default=None: ts if key == "owner_last_seen_at" else default)
    assert watchdog.check_owner_silence() == expected


def test_silence_returns_none_when_never_seen(monkeypatch):
    monkeypatch.setattr(settings_store, "get",
                        lambda key, default=None: None)
    assert watchdog.check_owner_silence() is None


def test_silence_handles_bad_format(monkeypatch):
    monkeypatch.setattr(settings_store, "get",
                        lambda key, default=None: "not-a-date" if key == "owner_last_seen_at" else default)
    assert watchdog.check_owner_silence() is None


def test_ping_healthchecks_returns_false_when_url_empty(monkeypatch):
    class _S:
        HEALTHCHECKS_PING_URL = ""
    monkeypatch.setattr(watchdog, "get_settings", lambda: _S())
    assert watchdog.ping_healthchecks() is False
