"""Синхронный send_alert через Telegram Bot API HTTP.

Используется как alert-sink для retry.py: вызывается из синхронного кода
воркеров, поэтому не упирается в aiogram-event-loop.
"""

from __future__ import annotations

import logging

import httpx

from src.core.config import get_settings

log = logging.getLogger(__name__)


def send_alert(message: str) -> None:
    s = get_settings()
    if not s.TELEGRAM_BOT_TOKEN or not s.TELEGRAM_OWNER_CHAT_ID:
        log.warning("send_alert skipped: telegram creds missing")
        return
    url = f"https://api.telegram.org/bot{s.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": s.TELEGRAM_OWNER_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True,
    }
    try:
        r = httpx.post(url, json=payload, timeout=10.0)
        if r.status_code >= 400:
            log.warning("telegram sendMessage %d: %s", r.status_code, r.text[:200])
    except httpx.HTTPError as exc:
        log.warning("telegram send_alert http error: %s", exc)
