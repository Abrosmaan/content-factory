"""Publisher worker (спецификация 6.8 ТЗ).

Цикл:
  1. claim publish_queued + scheduled_publish_at <= now() через
     SELECT FOR UPDATE SKIP LOCKED.
  2. скачать render.mp4 (и thumbnail для длинных) из R2 во временный файл.
  3. youtube.upload_video(privacyStatus=private, publishAt=scheduled_publish_at)
  4. set_thumbnail (для long-форматов)
  5. post_pinned_comment (с пометкой «pin manually» — YouTube API не даёт
     pin'нуть программно; владелец пин'ит руками во время воскресной сессии).
  6. сохранить youtube_video_id; transition publish_queued → published.

Бюджет: до прохождения YT-аудита публикуется только private (E3.3); после
api_audit_passed=true можно автоматически переключать в public — но это
владелец делает руками в воскресный дайджест-блок (E3.3 reminder).
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from src.bot.alerts import send_alert
from src.core import r2, settings_store, youtube
from src.core.config import require
from src.core.db import connection
from src.core.state_machine import StateMachine

log = logging.getLogger(__name__)

WORKER_NAME = "publisher"


def _claim() -> dict | None:
    sql = (
        "SELECT id::text, format::text, title, seo, render_r2_key, "
        "       thumbnail_r2_key, scheduled_publish_at "
        "FROM videos "
        "WHERE status = 'publish_queued' "
        "  AND scheduled_publish_at IS NOT NULL "
        "  AND scheduled_publish_at <= now() "
        "ORDER BY scheduled_publish_at "
        "FOR UPDATE SKIP LOCKED LIMIT 1"
    )
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "format": row[1], "title": row[2],
        "seo": row[3] or {}, "render_r2_key": row[4],
        "thumbnail_r2_key": row[5], "publish_at": row[6],
    }


def _save_published(conn, video_id: str, yt_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE videos SET youtube_video_id = %s WHERE id = %s",
            (yt_id, video_id),
        )


def _download(bucket: str, key: str, suffix: str) -> Path:
    cli = r2.client()
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    f.close()
    cli.download_file(bucket, key, f.name)
    return Path(f.name)


def process_video(record: dict) -> str:
    require("epic3")
    s = require("epic1")
    video_id = record["id"]
    fmt = record["format"]
    log.info("publisher start video=%s format=%s", video_id, fmt)

    render_key = record["render_r2_key"]
    if not render_key:
        StateMachine(WORKER_NAME).transition(
            video_id, "publish_queued", "failed",
            last_error="no render_r2_key on publish",
        )
        return "failed"

    seo = record["seo"]
    title = (seo.get("title") or record["title"] or "Untitled")[:100]
    description = seo.get("description") or ""
    tags = [t for t in (seo.get("tags") or []) if isinstance(t, str)]
    pinned = seo.get("pinned_comment") or ""

    mp4 = _download(s.R2_BUCKET_RENDERS, render_key, ".mp4")
    thumb_path: Path | None = None
    if record["thumbnail_r2_key"]:
        thumb_path = _download(s.R2_BUCKET_RENDERS, record["thumbnail_r2_key"], ".jpg")

    # E3.3: до прохождения YT-аудита остаёмся private. После — public.
    privacy = "public" if settings_store.is_audit_passed() else "private"
    try:
        yt_id = youtube.upload_video(
            file_path=str(mp4),
            title=title,
            description=description,
            tags=tags,
            publish_at=record["publish_at"],
            privacy_status=privacy,
        )
        log.info("publisher uploaded video=%s yt=%s privacy=%s",
                 video_id, yt_id, privacy)

        if thumb_path:
            try:
                youtube.set_thumbnail(yt_id, str(thumb_path))
            except Exception:
                log.warning("set_thumbnail failed", exc_info=True)

        if pinned:
            try:
                youtube.post_pinned_comment(yt_id, pinned + " ")
            except Exception:
                log.warning("post_pinned_comment failed", exc_info=True)

    finally:
        mp4.unlink(missing_ok=True)
        if thumb_path:
            thumb_path.unlink(missing_ok=True)

    with connection() as conn:
        with conn.transaction():
            _save_published(conn, video_id, yt_id)

    StateMachine(WORKER_NAME).transition(
        video_id, "publish_queued", "published",
        detail=f"youtube={yt_id}",
    )
    if privacy == "private":
        send_alert(
            f"📺 Published (private): {title}\nyoutube id: {yt_id}\n"
            f"Open YouTube Studio and pin the top comment manually, "
            f"and switch privacy to public when ready."
        )
    else:
        send_alert(
            f"📺 Published (public): {title}\nyoutube id: {yt_id}\n"
            f"Pin the top comment manually."
        )
    return "published"


def run_once() -> int:
    # Точечный kill-switch: если YouTube-загрузка выключена, не берём
    # работу (пайплайн может генерить видео дальше — они просто
    # копятся в ready_for_review).
    from src.core import settings_store
    if settings_store.is_youtube_upload_paused():
        return 0
    rec = _claim()
    if not rec:
        return 0
    try:
        process_video(rec)
        return 1
    except Exception:
        log.exception("publisher failed video=%s", rec["id"])
        return 0


__all__ = ["WORKER_NAME", "process_video", "run_once"]
