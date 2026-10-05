"""Юнит-тесты публикационных слотов (E3.2)."""

from __future__ import annotations

import datetime as dt

import pytest

from src.core import slots


UTC = dt.timezone.utc


def _at(y, m, d, h=0, mi=0) -> dt.datetime:
    return dt.datetime(y, m, d, h, mi, tzinfo=UTC)


# Тестовая неделя: 8 июня 2026 = понедельник.
# 2026-06-08 Mon, 09 Tue, 10 Wed, 11 Thu, 12 Fri, 13 Sat, 14 Sun.


def test_short_mon_morning_picks_same_day_17():
    s = slots.next_slot(_at(2026, 6, 8, 9, 0), "short")
    assert s == _at(2026, 6, 8, 17)


def test_short_mon_evening_picks_wednesday():
    s = slots.next_slot(_at(2026, 6, 8, 18, 0), "short")
    assert s == _at(2026, 6, 10, 17)  # wed


def test_short_thursday_picks_friday():
    s = slots.next_slot(_at(2026, 6, 11, 9, 0), "short")
    assert s == _at(2026, 6, 12, 17)


def test_long_gaming_mon_picks_tuesday_16():
    s = slots.next_slot(_at(2026, 6, 8, 9, 0), "long_gaming")
    assert s == _at(2026, 6, 9, 16)


def test_long_setup_uses_same_long_slots():
    s = slots.next_slot(_at(2026, 6, 8, 9, 0), "long_setup")
    assert s == _at(2026, 6, 9, 16)


def test_long_after_friday_wraps_to_next_tuesday():
    # суббота 13 — следующий long-слот вт 16
    s = slots.next_slot(_at(2026, 6, 13, 9, 0), "long_gaming")
    assert s == _at(2026, 6, 16, 16)


def test_lead_minutes_protects_from_past_slot():
    """Если now = вт 15:55 UTC и lead=10мин → слот 16:00 на грани не назначается."""
    s = slots.next_slot(_at(2026, 6, 9, 15, 55), "long_gaming",
                        lead_minutes=10)
    # 15:55 + 10мин = 16:05 → этот слот пропускаем, берём пятницу 16:00
    assert s == _at(2026, 6, 12, 16)


def test_rejects_naive_now():
    with pytest.raises(ValueError):
        slots.next_slot(dt.datetime(2026, 6, 8, 9, 0), "short")


def test_rejects_unknown_format():
    with pytest.raises(ValueError):
        slots.next_slot(_at(2026, 6, 8), "unknown_format")


def test_upcoming_slots_strictly_increasing():
    out = slots.upcoming_slots(_at(2026, 6, 8, 9, 0), "short", n=4)
    assert len(out) == 4
    for a, b in zip(out, out[1:]):
        assert a < b
