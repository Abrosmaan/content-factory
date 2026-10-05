"""Расчёт публикационных слотов (раздел 6.8 ТЗ).

Шортсы:  пн/ср/пт/сб 17:00 UTC
Длинные: вт/пт 16:00 UTC

`next_slot(now, format)` возвращает ближайший слот не раньше now+lead_minutes
(lead_minutes защищает от попытки опубликовать в прошлое из-за округлений).
"""

from __future__ import annotations

import datetime as dt
from typing import Iterable

# weekday(): Monday=0, ..., Sunday=6
_SHORT_DAYS = {0, 2, 4, 5}       # mon, wed, fri, sat
_SHORT_HOUR = 17                  # 17:00 UTC

_LONG_DAYS = {1, 4}              # tue, fri
_LONG_HOUR = 16                   # 16:00 UTC

_DAYS_BY_FORMAT: dict[str, tuple[set[int], int]] = {
    "short":       (_SHORT_DAYS, _SHORT_HOUR),
    "long_gaming": (_LONG_DAYS,  _LONG_HOUR),
    "long_setup":  (_LONG_DAYS,  _LONG_HOUR),
    "bridge":      (_LONG_DAYS,  _LONG_HOUR),
}


def next_slot(now: dt.datetime, fmt: str, *,
              lead_minutes: int = 10,
              max_lookahead_days: int = 14) -> dt.datetime:
    """Ближайший слот >= now+lead_minutes. now должен быть aware (UTC)."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    cfg = _DAYS_BY_FORMAT.get(fmt)
    if cfg is None:
        raise ValueError(f"unknown format: {fmt}")
    allowed_days, hour = cfg
    earliest = now + dt.timedelta(minutes=lead_minutes)
    cursor = earliest.astimezone(dt.timezone.utc)
    base = cursor.date()
    for offset in range(max_lookahead_days):
        d = base + dt.timedelta(days=offset)
        if d.weekday() not in allowed_days:
            continue
        slot = dt.datetime(d.year, d.month, d.day, hour, 0, 0,
                           tzinfo=dt.timezone.utc)
        if slot >= earliest:
            return slot
    raise RuntimeError(f"no slot found within {max_lookahead_days} days")


def upcoming_slots(now: dt.datetime, fmt: str, n: int = 5,
                   lead_minutes: int = 10) -> list[dt.datetime]:
    """Список следующих N слотов для формата (удобно для /digest)."""
    out: list[dt.datetime] = []
    cur = now
    for _ in range(n):
        s = next_slot(cur, fmt, lead_minutes=lead_minutes)
        out.append(s)
        cur = s + dt.timedelta(minutes=1)
    return out


__all__ = ["next_slot", "upcoming_slots"]
