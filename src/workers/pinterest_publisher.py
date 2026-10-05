"""Pinterest Publisher (спецификация 6.13 ТЗ).

Слоты: пн/ср/пт 14:00 UTC. Поллит distributions(channel='pinterest',
status='content_ready'). Для каждого:
  - image из R2 (signed URL), board по формату видео,
  - link на блог-статью (НЕ youtube),
  - POST /v5/pins.
≤2 пина за прогон. 429/600x → failed + alert.

[!] Smoke требует PINTEREST_ACCESS_TOKEN + board IDs.
"""

from __future__ import annotations

import logging

import httpx

from src.bot.alerts import send_alert
from src.core import r2
from src.core.config import require
from src.core.db import connection

log = logging.getLogger(__name__)

WORKER_NAME = "pinterest_publisher"
MAX_PER_RUN = 2


def _board_for(video_format: str) -> str:
    s = require("epic6_pinterest")
    if video_format == "long_setup":
        return s.PINTEREST_BOARD_ID_SETUP
    return s.PINTEREST_BOARD_ID_GAMING


def _claim_for_publish(limit: int = MAX_PER_RUN) -> list[dict]:
    sql = (
        "SELECT d.id::text, d.video_id::text, d.content, "
        "       v.format::text "
        "FROM distributions d JOIN videos v ON v.id = d.video_id "
        "JOIN distributions b ON b.video_id = v.id AND b.channel='blog' "
        "WHERE d.channel = 'pinterest' AND d.status = 'content_ready' "
        "  AND b.external_url IS NOT NULL "
        "ORDER BY d.id "
        "FOR UPDATE OF d SKIP LOCKED LIMIT %s"
    )
    out: list[dict] = []
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(sql, (limit,))
                for row in cur.fetchall():
                    cur.execute(
                        "SELECT external_url FROM distributions "
                        "WHERE video_id = %s AND channel = 'blog'",
                        (row[1],),
                    )
                    blog_url = cur.fetchone()
                    out.append({
                        "id": row[0], "video_id": row[1],
                        "content": row[2] or {},
                        "format": row[3],
                        "blog_url": blog_url[0] if blog_url else None,
                    })
    return out


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


def _create_pin(*, image_url: str, board_id: str,
                title: str, description: str,
                link: str) -> str:
    s = require("epic6_pinterest")
    body = {
        "board_id": board_id,
        "title": title[:100],
        "description": description[:500],
        "link": link,
        "media_source": {
            "source_type": "image_url",
            "url": image_url,
        },
    }
    r = httpx.post(
        "https://api.pinterest.com/v5/pins",
        json=body,
        headers={"Authorization": f"Bearer {s.PINTEREST_ACCESS_TOKEN}",
                 "Content-Type": "application/json"},
        timeout=30.0,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"pinterest {r.status_code}: {r.text[:300]}")
    pin = r.json()
    return f"https://www.pinterest.com/pin/{pin.get('id')}/"


def process_one(rec: dict) -> str:
    s_e1 = require("epic1")
    require("epic6_pinterest")

    content = rec["content"] or {}
    image_key = content.get("image_r2_key")
    if not image_key:
        log.warning("pinterest: no image_r2_key in content")
        with connection() as conn:
            with conn.transaction():
                _mark_failed(conn, rec["id"], "no image_r2_key in content")
        return "failed"

    image_url = r2.signed_url(s_e1.R2_BUCKET_RENDERS, image_key)
    board = _board_for(rec["format"])
    title = (content.get("title") or "Untitled")[:100]
    description = content.get("description") or ""
    link = rec.get("blog_url") or "https://example.com"

    try:
        pin_url = _create_pin(
            image_url=image_url, board_id=board,
            title=title, description=description, link=link,
        )
    except Exception as exc:
        log.warning("pinterest create failed: %s", exc)
        with connection() as conn:
            with conn.transaction():
                _mark_failed(conn, rec["id"], str(exc)[:500])
        send_alert(f"📌 Pinterest publish failed: {exc}")
        return "failed"

    with connection() as conn:
        with conn.transaction():
            _mark_posted(conn, rec["id"], pin_url)
    log.info("pinterest posted: %s", pin_url)
    return "posted"


def run_once() -> int:
    from src.core import settings_store
    if settings_store.is_distribution_paused():
        return 0
    picks = _claim_for_publish(MAX_PER_RUN)
    processed = 0
    for rec in picks:
        try:
            process_one(rec)
            processed += 1
        except Exception:
            log.exception("pinterest run_once failed for %s", rec["id"])
    return processed


__all__ = ["WORKER_NAME", "MAX_PER_RUN", "process_one", "run_once",
           "_board_for"]
