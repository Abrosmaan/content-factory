"""Analyst (спецификация 6.9 ТЗ).

Часть кода требует YouTube Analytics API (snapshot views/CTR/retention)
и блокирована отсутствием YT_REFRESH_TOKEN. Чистые вычисления
(пересчёт format_weights, kill-criterion) — code-ready и протестированы
на синтетике.

Расписание:
  ежедневно 05:00 UTC + воскресенье 05:30 (недельный свод; вызывается
  scheduler.add_job, см. E4.4).

Алгоритм:
  1. для каждого video с status='published' и youtube_video_id и
     status_updated_at >= now-30d:
       fetch_analytics(youtube_video_id) → INSERT в analytics snapshot
  2. recalculate format_weights (см. recalc_weights()): нормализованная
     функция от средних CTR/retention/subs_gained по последним snapshot'ам.
  3. kill-criterion: для каждого формата найти последние 3 video; если
     каждое имеет CTR<2.5% ИЛИ retention<35% → killed=true + Telegram alert.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from src.bot.alerts import send_alert
from src.core.db import connection

log = logging.getLogger(__name__)

WORKER_NAME = "analyst"

KILL_LAST_N = 3
KILL_CTR_THRESHOLD = 2.5      # %
KILL_RETENTION_THRESHOLD = 35  # %


# --------- pure recalc ---------

def recalc_weights(rows_by_format: dict[str, list[dict[str, Any]]],
                   ) -> dict[str, float]:
    """rows_by_format = {fmt: [{'ctr': float, 'retention_pct': float,
    'subs_gained': int}, ...]} → нормализованные weights."""
    if not rows_by_format:
        return {}
    formats = list(rows_by_format.keys())
    # средние по формату
    averages: dict[str, float] = {}
    for fmt, rows in rows_by_format.items():
        if not rows:
            averages[fmt] = 0.0
            continue
        n = len(rows)
        avg_ctr = sum(r.get("ctr", 0) or 0 for r in rows) / n
        avg_ret = sum(r.get("retention_pct", 0) or 0 for r in rows) / n
        avg_sub = sum(r.get("subs_gained", 0) or 0 for r in rows) / n
        # CTR ~5% и retention ~50% и subs/видео ~20 — это «хорошо»
        # Нормализованная weighted sum
        score = (avg_ctr / 5.0) * 0.4 + (avg_ret / 50.0) * 0.4 + \
                (avg_sub / 20.0) * 0.2
        averages[fmt] = max(0.0, score)

    # ренормировка к среднему 1.0
    if not averages or all(v == 0 for v in averages.values()):
        return {f: 1.0 for f in formats}
    mean = sum(averages.values()) / len(averages)
    if mean <= 0:
        return {f: 1.0 for f in formats}
    return {f: v / mean for f, v in averages.items()}


def check_kill_criterion(rows: list[dict[str, Any]]) -> bool:
    """rows: последние N видео формата (свежие сверху). Каждое запись с
    'ctr' и 'retention_pct'. True → формат «убит»."""
    if len(rows) < KILL_LAST_N:
        return False
    last = rows[:KILL_LAST_N]
    for r in last:
        ctr = (r.get("ctr") or 0) * 1.0
        ret = (r.get("retention_pct") or 0) * 1.0
        if ctr >= KILL_CTR_THRESHOLD and ret >= KILL_RETENTION_THRESHOLD:
            return False
    return True


# --------- БД interactions ---------

def _fetch_recent_snapshots_by_format(days: int = 60) -> dict[str, list[dict]]:
    sql = (
        "SELECT v.format::text, a.ctr, a.retention_pct, a.subs_gained, "
        "       a.snapshot_at "
        "FROM analytics a JOIN videos v ON v.id = a.video_id "
        "WHERE a.snapshot_at >= now() - (%s::int || ' days')::interval "
        "ORDER BY a.snapshot_at DESC"
    )
    out: dict[str, list[dict]] = defaultdict(list)
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (days,))
            for fmt, ctr, ret, subs, ts in cur.fetchall():
                out[fmt].append({
                    "ctr": float(ctr) if ctr is not None else 0.0,
                    "retention_pct": float(ret) if ret is not None else 0.0,
                    "subs_gained": int(subs) if subs is not None else 0,
                    "snapshot_at": ts,
                })
    return dict(out)


def save_weights(weights: dict[str, float]) -> None:
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                for fmt, w in weights.items():
                    cur.execute(
                        "INSERT INTO format_weights (format, weight) "
                        "VALUES (%s::video_format, %s) "
                        "ON CONFLICT (format) DO UPDATE "
                        "SET weight = EXCLUDED.weight, updated_at = now()",
                        (fmt, w),
                    )


def mark_killed(fmt: str) -> None:
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE format_weights SET killed = TRUE, updated_at = now() "
                    "WHERE format = %s::video_format",
                    (fmt,),
                )
    send_alert(
        f"☠️ Format killed: {fmt} — last {KILL_LAST_N} videos all under "
        f"CTR {KILL_CTR_THRESHOLD}% or retention {KILL_RETENTION_THRESHOLD}%."
    )


# --------- YouTube Analytics API ---------

def _yt_analytics_client():
    from googleapiclient.discovery import build  # type: ignore
    from src.core.youtube import _credentials
    return build("youtubeAnalytics", "v2", credentials=_credentials(),
                 cache_discovery=False)


def fetch_youtube_snapshot(youtube_video_id: str,
                           *, days_back: int = 7) -> dict[str, Any] | None:
    """Один snapshot метрик для video. Возвращает None если данных нет ещё
    (свежее видео без impressions)."""
    import datetime as dt
    end = dt.date.today()
    start = end - dt.timedelta(days=days_back)
    metrics = ",".join([
        "views", "estimatedMinutesWatched", "averageViewDuration",
        "subscribersGained", "impressions",
        "annotationClickThroughRate",  # CTR thumbnails
        "audienceWatchRatio",          # retention %
    ])
    try:
        resp = _yt_analytics_client().reports().query(
            ids="channel==MINE",
            startDate=start.isoformat(),
            endDate=end.isoformat(),
            metrics=metrics,
            filters=f"video=={youtube_video_id}",
        ).execute()
    except Exception:
        log.exception("yt analytics query failed video=%s", youtube_video_id)
        return None
    rows = resp.get("rows") or []
    if not rows:
        return None
    cols = [c["name"] for c in resp.get("columnHeaders", [])]
    row = rows[0]
    data = dict(zip(cols, row))
    impressions = float(data.get("impressions") or 0)
    views = float(data.get("views") or 0)
    avd = float(data.get("averageViewDuration") or 0)
    ctr_pct = (
        100.0 * float(data.get("annotationClickThroughRate") or 0)
        if data.get("annotationClickThroughRate") else None
    )
    retention_pct = (
        100.0 * float(data.get("audienceWatchRatio") or 0)
        if data.get("audienceWatchRatio") else None
    )
    return {
        "views": int(views),
        "impressions": int(impressions),
        "ctr": ctr_pct,
        "avg_view_duration_sec": avd,
        "retention_pct": retention_pct,
        "subs_gained": int(float(data.get("subscribersGained") or 0)),
        "estimated_minutes_watched": float(
            data.get("estimatedMinutesWatched") or 0),
    }


def _save_snapshot(video_id: str, snap: dict[str, Any]) -> None:
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO analytics (video_id, views, impressions, "
                    "       ctr, avg_view_duration_sec, retention_pct, subs_gained) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (video_id, snap["views"], snap["impressions"],
                     snap["ctr"], snap["avg_view_duration_sec"],
                     snap["retention_pct"], snap["subs_gained"]),
                )


def _videos_for_snapshot() -> list[tuple[str, str]]:
    """Возвращает [(video_id, youtube_video_id)] published за 30д."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text, youtube_video_id FROM videos "
                "WHERE status IN ('published', 'analyzed') "
                "  AND youtube_video_id IS NOT NULL "
                "  AND status_updated_at >= now() - interval '30 days'"
            )
            return [(r[0], r[1]) for r in cur.fetchall()]


def fetch_all_snapshots() -> int:
    """Берёт всех published за 30д, делает snapshot, INSERT в analytics."""
    count = 0
    for vid, yt_id in _videos_for_snapshot():
        snap = fetch_youtube_snapshot(yt_id)
        if snap is None:
            log.info("analyst: no data yet for video=%s yt=%s", vid, yt_id)
            continue
        _save_snapshot(vid, snap)
        count += 1
    return count


def run_once() -> dict[str, Any]:
    """Полный прогон: fetch YT API → snapshots → weights/kill."""
    fetched = fetch_all_snapshots()
    snaps = _fetch_recent_snapshots_by_format(days=30)
    stats: dict[str, Any] = {
        "fetched": fetched,
        "formats": list(snaps.keys()),
        "killed": [],
    }

    if snaps:
        weights = recalc_weights(snaps)
        save_weights(weights)
        stats["weights"] = weights

    for fmt, rows in snaps.items():
        if check_kill_criterion(rows):
            mark_killed(fmt)
            stats["killed"].append(fmt)

    log.info("analyst: %s", stats)
    return stats


__all__ = [
    "WORKER_NAME",
    "recalc_weights",
    "check_kill_criterion",
    "run_once",
    "save_weights",
    "mark_killed",
]
