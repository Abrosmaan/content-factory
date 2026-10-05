"""Воскресный дайджест (раздел 7 ТЗ, спецификация E4.3).

Сборка отчёта + рассылка владельцу:
  1. summary message (5-7 строк аналитики)
  2. карточки до 10 pending_approval тем (с кнопками Одобрить/Отклонить/Правка)
  3. карточки видео на ревью (ready_for_review)
  4. блок дистрибуции (заглушка до E6)
  5. напоминания: private-видео (E3.3), footage-дыры

Поллит ничего — это командно-cron-триггер.
"""

from __future__ import annotations

import datetime as dt
import logging

import httpx

from src.bot.cards import send_topic_review_card, send_video_review_card
from src.bot.handlers import private_published_reminder
from src.core import r2
from src.core.config import get_settings
from src.core.db import connection

log = logging.getLogger(__name__)


# ---------- helpers ----------

def _send_text(text: str) -> None:
    s = get_settings()
    if not s.TELEGRAM_BOT_TOKEN or not s.TELEGRAM_OWNER_CHAT_ID:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{s.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": s.TELEGRAM_OWNER_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        log.warning("digest send_text: %s", exc)


# ---------- queries ----------

def _published_last_week() -> int:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM videos "
                "WHERE status = 'published' "
                "  AND status_updated_at >= now() - interval '7 days'"
            )
            return int(cur.fetchone()[0])


def _format_counts() -> dict[str, int]:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status::text, COUNT(*) FROM videos GROUP BY status"
            )
            return {row[0]: int(row[1]) for row in cur.fetchall()}


def _footage_gap_recent() -> list[str]:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT last_error FROM videos "
                "WHERE status = 'failed' "
                "  AND last_error LIKE 'footage_gap:%' "
                "  AND status_updated_at >= now() - interval '14 days'"
            )
            return [row[0] for row in cur.fetchall()]


def _unresolved_errors() -> int:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM errors WHERE NOT resolved")
            return int(cur.fetchone()[0])


def _pending_topics(limit: int = 10) -> list[dict]:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT t.id::text, t.title, t.score, t.score_reasoning, "
                "       t.suggested_format::text "
                "FROM topics t "
                "JOIN videos v ON v.topic_id = t.id "
                "WHERE v.status = 'pending_approval' "
                "ORDER BY t.score DESC NULLS LAST "
                "LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
    return [
        {"id": r[0], "title": r[1], "score": r[2],
         "reasoning": r[3], "fmt": r[4]}
        for r in rows
    ]


def _ready_videos() -> list[dict]:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text, title, COALESCE(seo, '{}'::jsonb), "
                "       render_r2_key "
                "FROM videos WHERE status = 'ready_for_review' "
                "ORDER BY status_updated_at"
            )
            rows = cur.fetchall()
    out = []
    for vid, title, seo, key in rows:
        out.append({"id": vid, "title": title, "seo": seo, "render_key": key})
    return out


# ---------- summary ----------

def build_summary() -> str:
    published = _published_last_week()
    counts = _format_counts()
    errors = _unresolved_errors()
    gaps = _footage_gap_recent()

    in_flight = sum(
        counts.get(s, 0)
        for s in ("approved", "scripted", "voiced", "assembled",
                  "packaged", "publish_queued")
    )
    ready = counts.get("ready_for_review", 0)
    pending = counts.get("pending_approval", 0)

    lines: list[str] = ["<b>📊 Weekly digest</b>"]
    lines.append(
        f"• published last 7d: <b>{published}</b>; "
        f"in-flight: {in_flight}; ready for review: {ready}; "
        f"pending topics: {pending}"
    )
    lines.append(f"• errors unresolved: {errors}; footage gaps: {len(gaps)}")
    # distribution stub (E6 заполнит)
    lines.append("• distribution: blog 0 · pinterest 0 · threads 0  (epic 6)")

    # private reminder (E3.3)
    rem = private_published_reminder()
    if rem:
        lines.append("")
        lines.append("🔒 " + rem)

    if gaps:
        lines.append("")
        lines.append("📭 footage gaps (record these):")
        for g in gaps[:5]:
            lines.append(f"  · {g[:120]}")

    return "\n".join(lines)


# ---------- send pipeline ----------

def send_digest(*, topic_limit: int = 10, force: bool = False) -> dict:
    """Полный дайджест: summary + темы + ready видео + заметки."""
    stats = {"topics": 0, "videos": 0}
    _send_text(build_summary())

    topics = _pending_topics(limit=topic_limit)
    for t in topics:
        try:
            send_topic_review_card(
                topic_id=t["id"], title=t["title"],
                score=t["score"], reasoning=t["reasoning"],
                suggested_format=t["fmt"],
            )
            stats["topics"] += 1
        except Exception:
            log.warning("digest topic card failed", exc_info=True)

    videos = _ready_videos()
    s = get_settings()
    for v in videos:
        try:
            preview = None
            key = v["render_key"]
            if key:
                if r2.object_exists(s.R2_BUCKET_RENDERS, key):
                    preview = r2.signed_url(s.R2_BUCKET_RENDERS, key)
                else:
                    log.warning("digest preview skipped: %s missing in R2", key)
            description = (v["seo"] or {}).get("description", "") if isinstance(v["seo"], dict) else ""
            short_desc = description.split("\n\n", 1)[0][:400]
            send_video_review_card(
                video_id=v["id"], title=v["title"] or "Untitled",
                short_description=short_desc, preview_url=preview,
            )
            stats["videos"] += 1
        except Exception:
            log.warning("digest video card failed", exc_info=True)

    return stats


__all__ = ["build_summary", "send_digest"]
