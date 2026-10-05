"""Threads Publisher (спецификация 6.14 ТЗ).

Только при threads_enabled=true. Двухшаговая публикация Meta Threads API:
  1) POST /v1.0/{user_id}/threads → создание container
  2) POST /v1.0/{user_id}/threads_publish → публикация container'а

Авто-рефреш long-lived токена каждые ~50 дней — функция refresh_token()
вызывается из watchdog.

[!] Smoke требует THREADS_USER_ID + THREADS_ACCESS_TOKEN.
"""

from __future__ import annotations

import logging

import httpx

from src.bot.alerts import send_alert
from src.core import settings_store
from src.core.config import require
from src.core.db import connection

log = logging.getLogger(__name__)

WORKER_NAME = "threads_publisher"


def _claim_for_publish() -> dict | None:
    sql = (
        "SELECT d.id::text, d.video_id::text, d.content "
        "FROM distributions d "
        "WHERE d.channel = 'threads' AND d.status = 'content_ready' "
        "ORDER BY d.id "
        "FOR UPDATE OF d SKIP LOCKED LIMIT 1"
    )
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "video_id": row[1], "content": row[2] or {}}


def _mark_posted(conn, dist_id: str, external_url: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE distributions SET status='posted', "
            "       external_url=%s, posted_at=now() WHERE id=%s",
            (external_url, dist_id),
        )


def _mark_failed(conn, dist_id: str, error: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE distributions SET status='failed', "
            "       last_error=%s, attempts = attempts + 1 WHERE id=%s",
            (error, dist_id),
        )


def _create_container(text: str) -> str:
    s = require("epic6_threads")
    r = httpx.post(
        f"https://graph.threads.net/v1.0/{s.THREADS_USER_ID}/threads",
        params={"media_type": "TEXT", "text": text[:500],
                "access_token": s.THREADS_ACCESS_TOKEN},
        timeout=30.0,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"threads container {r.status_code}: {r.text[:300]}")
    return r.json()["id"]


def _publish_container(container_id: str) -> str:
    s = require("epic6_threads")
    r = httpx.post(
        f"https://graph.threads.net/v1.0/{s.THREADS_USER_ID}/threads_publish",
        params={"creation_id": container_id,
                "access_token": s.THREADS_ACCESS_TOKEN},
        timeout=30.0,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"threads publish {r.status_code}: {r.text[:300]}")
    return r.json()["id"]


def refresh_token() -> str | None:
    """Long-lived token refresh — вызывается из watchdog каждые ~50 дней."""
    s = require("epic6_threads")
    r = httpx.get(
        "https://graph.threads.net/refresh_access_token",
        params={"grant_type": "th_refresh_token",
                "access_token": s.THREADS_ACCESS_TOKEN},
        timeout=30.0,
    )
    if r.status_code >= 400:
        log.warning("threads refresh failed: %s", r.text[:200])
        return None
    return r.json().get("access_token")


def process_one(rec: dict) -> str:
    if not settings_store.is_threads_enabled():
        # На уровне репурпозера уже выставляется skipped, но защитимся
        with connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE distributions SET status='skipped' "
                        "WHERE id=%s",
                        (rec["id"],),
                    )
        return "skipped"

    require("epic6_threads")
    content = rec["content"] or {}
    text = (content.get("text") or "")[:500]
    try:
        container = _create_container(text)
        thread_id = _publish_container(container)
    except Exception as exc:
        log.warning("threads publish failed: %s", exc)
        with connection() as conn:
            with conn.transaction():
                _mark_failed(conn, rec["id"], str(exc)[:500])
        send_alert(f"🧵 Threads publish failed: {exc}")
        return "failed"

    url = f"https://www.threads.net/@me/post/{thread_id}"
    with connection() as conn:
        with conn.transaction():
            _mark_posted(conn, rec["id"], url)
    return "posted"


def run_once() -> int:
    from src.core import settings_store
    if settings_store.is_distribution_paused():
        return 0
    rec = _claim_for_publish()
    if not rec:
        return 0
    try:
        process_one(rec)
        return 1
    except Exception:
        log.exception("threads publisher run_once failed")
        return 0


__all__ = [
    "WORKER_NAME",
    "process_one",
    "run_once",
    "refresh_token",
]
