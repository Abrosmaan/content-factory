"""Helpers для review-карточек (E3.1, E3.4): отправка карточек в Telegram
через bot HTTP API (синхронный httpx) с inline-keyboard.

Использовать из синхронных воркеров (seo_packager, scheduler), не из async
event-loop'а aiogram.
"""

from __future__ import annotations

import html
import json
import logging

import httpx

from src.core.config import get_settings

log = logging.getLogger(__name__)

# Callback-data префиксы. Длина ≤ 64 байта по правилам Telegram.
CB_VIDEO_APPROVE = "vap"  # vap:<uuid>
CB_VIDEO_REJECT  = "vrj"
CB_VIDEO_EDIT    = "ved"

CB_TOPIC_APPROVE = "tap"
CB_TOPIC_REJECT  = "trj"
CB_TOPIC_EDIT    = "ted"


def _api(method: str, payload: dict) -> dict:
    s = get_settings()
    if not s.TELEGRAM_BOT_TOKEN:
        log.warning("telegram token missing — card not sent")
        return {"ok": False}
    url = f"https://api.telegram.org/bot{s.TELEGRAM_BOT_TOKEN}/{method}"
    try:
        r = httpx.post(url, json=payload, timeout=15.0)
        data = r.json()
        if not data.get("ok"):
            log.warning("telegram %s failed: %s", method, data)
        return data
    except httpx.HTTPError as exc:
        log.warning("telegram %s http error: %s", method, exc)
        return {"ok": False}


def _video_keyboard(video_id: str) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "✅ Публиковать", "callback_data": f"{CB_VIDEO_APPROVE}:{video_id}"},
            {"text": "❌ Отклонить",   "callback_data": f"{CB_VIDEO_REJECT}:{video_id}"},
            {"text": "✏️ Правка",      "callback_data": f"{CB_VIDEO_EDIT}:{video_id}"},
        ]]
    }


def _topic_keyboard(topic_id: str) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "✅ Одобрить",  "callback_data": f"{CB_TOPIC_APPROVE}:{topic_id}"},
            {"text": "❌ Отклонить", "callback_data": f"{CB_TOPIC_REJECT}:{topic_id}"},
            {"text": "✏️ Правка",    "callback_data": f"{CB_TOPIC_EDIT}:{topic_id}"},
        ]]
    }


def send_video_review_card(
    *,
    video_id: str,
    title: str,
    short_description: str,
    preview_url: str | None,
) -> dict:
    """Карточка ready_for_review с тремя кнопками (E3.1)."""
    s = get_settings()
    if not s.TELEGRAM_OWNER_CHAT_ID:
        return {"ok": False}
    preview_line = (
        f"\n\n🎬 Preview (7d): {html.escape(preview_url)}" if preview_url else ""
    )
    text = (
        f"🆕 Ready for review\n\n"
        f"<b>{html.escape(title)}</b>\n\n"
        f"{html.escape(short_description)}{preview_line}\n\n"
        f"id: <code>{html.escape(video_id)}</code>"
    )
    return _api("sendMessage", {
        "chat_id": s.TELEGRAM_OWNER_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
        "reply_markup": _video_keyboard(video_id),
    })


def send_topic_review_card(
    *,
    topic_id: str,
    title: str,
    score: float | None,
    reasoning: str | None,
    suggested_format: str | None,
) -> dict:
    """Карточка темы pending_approval с тремя кнопками (E3.4)."""
    s = get_settings()
    if not s.TELEGRAM_OWNER_CHAT_ID:
        return {"ok": False}
    score_line = (
        f"score: <b>{(score or 0):.0f}/100</b>  · "
        f"format: <code>{html.escape(suggested_format or '?')}</code>"
    )
    text = (
        f"💡 New topic\n\n"
        f"<b>{html.escape(title)}</b>\n"
        f"{score_line}\n\n"
        f"{html.escape((reasoning or '').strip()[:600])}\n\n"
        f"id: <code>{html.escape(topic_id)}</code>"
    )
    return _api("sendMessage", {
        "chat_id": s.TELEGRAM_OWNER_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": _topic_keyboard(topic_id),
    })


def parse_callback(data: str) -> tuple[str, str] | None:
    """`vap:<uuid>` → ('vap', '<uuid>'). Возвращает None при невалидном формате."""
    if not data or ":" not in data:
        return None
    prefix, _, rest = data.partition(":")
    if not rest:
        return None
    return prefix, rest


__all__ = [
    "CB_VIDEO_APPROVE", "CB_VIDEO_REJECT", "CB_VIDEO_EDIT",
    "CB_TOPIC_APPROVE", "CB_TOPIC_REJECT", "CB_TOPIC_EDIT",
    "send_video_review_card",
    "send_topic_review_card",
    "parse_callback",
]
