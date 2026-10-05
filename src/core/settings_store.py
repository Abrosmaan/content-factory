"""Доступ к конфиг-таблице settings (key/value jsonb).

Используется для рантайм-флагов, которые меняются без редеплоя:
pause-флаг пайплайна, api_audit_passed, threads_enabled, счётчики токенов.
"""

from __future__ import annotations

import json
from typing import Any

from src.core.db import connection


def get(key: str, default: Any = None) -> Any:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM settings WHERE key = %s", (key,))
            row = cur.fetchone()
    if row is None:
        return default
    return row[0]


def set_value(key: str, value: Any) -> None:
    payload = json.dumps(value)
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO settings (key, value, updated_at) "
                    "VALUES (%s, %s::jsonb, now()) "
                    "ON CONFLICT (key) DO UPDATE "
                    "SET value = EXCLUDED.value, updated_at = now()",
                    (key, payload),
                )


def get_flag(key: str, default: bool = False) -> bool:
    val = get(key, default)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() == "true"
    return bool(val)


def set_flag(key: str, value: bool) -> None:
    set_value(key, bool(value))


def increment(key: str, by: int = 1) -> int:
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO settings (key, value, updated_at) "
                    "VALUES (%s, to_jsonb(%s::bigint), now()) "
                    "ON CONFLICT (key) DO UPDATE "
                    "SET value = to_jsonb((settings.value::text)::bigint + EXCLUDED.value::text::bigint), "
                    "    updated_at = now() "
                    "RETURNING value",
                    (key, by),
                )
                row = cur.fetchone()
    return int(row[0]) if row else by


# --- pause helpers --------------------------------------------------------
PAUSE_KEY = "pipeline_paused"
AUDIT_KEY = "api_audit_passed"

# Точечные kill-switches — каждый гейтит свою подсистему независимо
# от глобального pipeline_paused. Формат: settings.key = bool.
YOUTUBE_UPLOAD_PAUSED_KEY = "youtube_upload_paused"
DISTRIBUTION_PAUSED_KEY = "distribution_paused"
TEXT_NEWS_PAUSED_KEY = "text_news_paused"

# Все известные switches — используется в /switches UI и тестах,
# чтобы источник истины был один.
SWITCH_KEYS: tuple[tuple[str, str], ...] = (
    (PAUSE_KEY, "весь производственный пайплайн"),
    (YOUTUBE_UPLOAD_PAUSED_KEY, "YouTube-публикация"),
    (DISTRIBUTION_PAUSED_KEY, "дистрибуция (blog / pinterest / threads)"),
    (TEXT_NEWS_PAUSED_KEY, "текстовый новостной канал"),
)


def is_paused() -> bool:
    return get_flag(PAUSE_KEY, default=False)


def pause() -> None:
    set_flag(PAUSE_KEY, True)


def resume() -> None:
    set_flag(PAUSE_KEY, False)


def is_youtube_upload_paused() -> bool:
    return get_flag(YOUTUBE_UPLOAD_PAUSED_KEY, default=False)


def is_distribution_paused() -> bool:
    return get_flag(DISTRIBUTION_PAUSED_KEY, default=False)


def is_text_news_paused() -> bool:
    return get_flag(TEXT_NEWS_PAUSED_KEY, default=False)


def toggle_switch(key: str) -> bool:
    """Флип флага; возвращает новое состояние."""
    current = get_flag(key, default=False)
    new_value = not current
    set_flag(key, new_value)
    return new_value


# --- YT audit helpers (E3.3) ---------------------------------------------

def is_audit_passed() -> bool:
    return get_flag(AUDIT_KEY, default=False)


def set_audit_passed(value: bool) -> None:
    set_flag(AUDIT_KEY, value)


THREADS_KEY = "threads_enabled"


def is_threads_enabled() -> bool:
    return get_flag(THREADS_KEY, default=False)


def set_threads_enabled(value: bool) -> None:
    set_flag(THREADS_KEY, value)
