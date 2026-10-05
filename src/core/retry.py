"""Ретраи + dead-letter (раздел 6 ТЗ — «все сетевые вызовы через retry.py»).

3 попытки, exponential backoff 5/25/125 секунд. На третьем фейле:
  • запись в `errors` (worker, video_id, error, payload, resolved=false)
  • перевод видео в `failed` через StateMachine
  • вызов alert-hook (Telegram — подключается в E1.5)

Алерт-хук передаётся аргументом или модульной переменной, чтобы не
создавать циклической зависимости с `src.bot`.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from src.core.db import connection
from src.core.state_machine import StateMachine

log = logging.getLogger(__name__)

T = TypeVar("T")

# По умолчанию задержки между попытками (секунды) — ТЗ 6 раздел.
DEFAULT_DELAYS: tuple[int, ...] = (5, 25, 125)
DEFAULT_MAX_ATTEMPTS = 3


class AlertSink(Protocol):
    def __call__(self, message: str) -> None: ...


_alert_sink: AlertSink | None = None


def set_alert_sink(sink: AlertSink | None) -> None:
    """Регистрирует Telegram-хук. Вызывается из bot/main.py при старте."""
    global _alert_sink
    _alert_sink = sink


def _emit_alert(message: str) -> None:
    if _alert_sink is None:
        log.warning("alert skipped (no sink registered): %s", message)
        return
    try:
        _alert_sink(message)
    except Exception:  # хук не должен мешать пайплайну
        log.exception("alert sink raised")


class DeadLetter(RuntimeError):
    """Бросается после исчерпания ретраев. Поймана воркером — пайплайн идёт дальше."""

    def __init__(self, worker: str, video_id: str | None, original: BaseException) -> None:
        super().__init__(f"{worker} dead-letter for video={video_id}: {original}")
        self.worker = worker
        self.video_id = video_id
        self.original = original


@dataclass
class RetryConfig:
    worker: str
    video_id: str | None = None
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    delays: tuple[int, ...] = DEFAULT_DELAYS
    # None → используется time.sleep при каждом вызове (resolves через модуль,
    # чтобы monkeypatch в тестах работал).
    sleep: Callable[[float], None] | None = None
    # state-machine управление: с какого статуса переходить в failed
    fail_from_status: str | None = None
    payload: dict[str, Any] | None = None
    # Сразу fail без ретраев: bad request, auth, permission и т.п. — retry не лечит.
    non_retryable: tuple[type[BaseException], ...] = ()


def run_with_retry(fn: Callable[[], T], cfg: RetryConfig) -> T:
    last_exc: BaseException | None = None
    for attempt in range(1, cfg.max_attempts + 1):
        try:
            return fn()
        except cfg.non_retryable as exc:
            last_exc = exc
            log.warning(
                "%s non-retryable on attempt %d video=%s: %s",
                cfg.worker, attempt, cfg.video_id, exc,
            )
            break
        except Exception as exc:  # noqa: BLE001 — нам нужно ловить всё
            last_exc = exc
            log.warning(
                "%s attempt %d/%d failed video=%s: %s",
                cfg.worker, attempt, cfg.max_attempts, cfg.video_id, exc,
            )
            if attempt >= cfg.max_attempts:
                break
            delay = cfg.delays[min(attempt - 1, len(cfg.delays) - 1)]
            (cfg.sleep or time.sleep)(delay)

    assert last_exc is not None
    _record_dead_letter(cfg, last_exc)
    raise DeadLetter(cfg.worker, cfg.video_id, last_exc) from last_exc


def _record_dead_letter(cfg: RetryConfig, exc: BaseException) -> None:
    payload_json = json.dumps(cfg.payload or {}, default=str)
    err_text = f"{type(exc).__name__}: {exc}"

    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO errors (worker, video_id, error, payload) "
                    "VALUES (%s, %s, %s, %s::jsonb)",
                    (cfg.worker, cfg.video_id, err_text, payload_json),
                )
                if cfg.video_id is not None:
                    cur.execute(
                        "UPDATE videos SET attempts = attempts + 1 WHERE id = %s",
                        (cfg.video_id,),
                    )

    # Перевод в failed — отдельной транзакцией через StateMachine
    if cfg.video_id is not None and cfg.fail_from_status is not None:
        try:
            StateMachine(cfg.worker).transition(
                cfg.video_id,
                cfg.fail_from_status,
                "failed",
                last_error=err_text,
            )
        except Exception:
            log.exception("failed to transition %s -> failed", cfg.video_id)

    _emit_alert(
        f"[dead-letter] {cfg.worker} video={cfg.video_id}\n{err_text}"
    )


__all__ = [
    "AlertSink",
    "DeadLetter",
    "RetryConfig",
    "DEFAULT_DELAYS",
    "DEFAULT_MAX_ATTEMPTS",
    "run_with_retry",
    "set_alert_sink",
]
