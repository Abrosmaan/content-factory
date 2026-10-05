"""Единственная точка смены статуса видео-записи (раздел 2 ТЗ, константа 1).

Контракт:
  • валидация перехода по таблице ALLOWED_TRANSITIONS
  • запись в status_log каждой смены
  • захват работы через SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1 — два
    воркера не могут забрать одну и ту же запись

Прямые UPDATE статусов в обход StateMachine — нарушение архитектуры.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

from src.core.db import connection
from src.core import settings_store

log = logging.getLogger(__name__)

# Каноничные имена статусов — должны 1-в-1 совпадать с enum video_status.
STATUSES: tuple[str, ...] = (
    "raw",
    "scored",
    "pending_approval",
    "approved",
    "scripted",
    "footage_ready",
    "voiced",
    "assembled",
    "packaged",
    "ready_for_review",
    "publish_queued",
    "published",
    "analyzed",
    "rejected",
    "failed",
)

# Разрешённые переходы. Кроме перечисленных любой `<x> → failed` тоже
# разрешён (см. _failed_allowed_from). Терминальные статусы — без выхода.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "raw":              frozenset({"scored"}),
    "scored":           frozenset({"pending_approval"}),
    "pending_approval": frozenset({"approved", "rejected"}),
    "approved":         frozenset({"scripted"}),
    # scripted → footage_ready: long_gaming passthrough в scriptwriter;
    # short/long_setup ждут footage_generator (E8.4) перед voice.
    "scripted":         frozenset({"footage_ready"}),
    "footage_ready":    frozenset({"voiced"}),
    "voiced":           frozenset({"assembled"}),
    "assembled":        frozenset({"packaged"}),
    "packaged":         frozenset({"ready_for_review"}),
    # «Правка» владельца возвращает запись в approved, чтобы Scriptwriter
    # перезапустил с директивой (E3.1).
    "ready_for_review": frozenset({"publish_queued", "rejected", "approved"}),
    "publish_queued":   frozenset({"published"}),
    "published":        frozenset({"analyzed"}),
    "analyzed":         frozenset(),
    "rejected":         frozenset(),
    "failed":           frozenset(),
}

# Откуда вообще можно уйти в failed. Терминальные статусы не уходят.
_FAILED_FORBIDDEN_FROM: frozenset[str] = frozenset({"rejected", "failed", "analyzed"})


class InvalidTransition(ValueError):
    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__(f"forbidden transition: {from_status} -> {to_status}")
        self.from_status = from_status
        self.to_status = to_status


class VideoNotFound(LookupError):
    pass


def is_allowed(from_status: str, to_status: str) -> bool:
    if from_status not in ALLOWED_TRANSITIONS:
        return False
    if to_status not in STATUSES:
        return False
    if to_status == "failed":
        return from_status not in _FAILED_FORBIDDEN_FROM
    return to_status in ALLOWED_TRANSITIONS[from_status]


def validate(from_status: str, to_status: str) -> None:
    if not is_allowed(from_status, to_status):
        raise InvalidTransition(from_status, to_status)


@dataclass(frozen=True)
class Claimed:
    video_id: str
    status: str
    attempts: int
    format: str | None
    payload: dict[str, Any]


class StateMachine:
    """Обёртка над transitions для конкретного воркера."""

    def __init__(self, actor: str) -> None:
        if not actor:
            raise ValueError("actor must be non-empty")
        self.actor = actor

    # ------- захват -------
    def claim(self, status: str, *, limit: int = 1) -> list[Claimed]:
        """Достать до `limit` записей в указанном статусе с блокировкой.

        Использует FOR UPDATE SKIP LOCKED — параллельные воркеры не получат
        одну и ту же запись. Возвращает уже захваченные строки; вызывающий
        код должен сделать transition в той же транзакции — но здесь
        соединение закрывается, поэтому захват короткий и для длинных
        операций воркер должен использовать собственный transaction-блок.
        """
        if status not in ALLOWED_TRANSITIONS:
            raise ValueError(f"unknown status: {status}")
        # Pause-флаг: воркеры не захватывают новую работу пока пайплайн на паузе.
        try:
            if settings_store.is_paused():
                return []
        except Exception:
            log.warning("pause flag unreadable; proceeding", exc_info=True)
        sql = (
            "SELECT id, status, attempts, format::text "
            "FROM videos WHERE status = %s "
            "ORDER BY status_updated_at "
            "FOR UPDATE SKIP LOCKED LIMIT %s"
        )
        rows: list[Claimed] = []
        with connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(sql, (status, limit))
                    for vid, st, attempts, fmt in cur.fetchall():
                        rows.append(
                            Claimed(
                                video_id=str(vid),
                                status=st,
                                attempts=attempts or 0,
                                format=fmt,
                                payload={},
                            )
                        )
        return rows

    # ------- смена статуса -------
    def transition(
        self,
        video_id: str,
        from_status: str,
        to_status: str,
        *,
        detail: str | None = None,
        last_error: str | None = None,
        bump_attempts: bool = False,
    ) -> None:
        validate(from_status, to_status)
        with connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT status FROM videos WHERE id = %s FOR UPDATE",
                        (video_id,),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise VideoNotFound(video_id)
                    actual = row[0]
                    if actual != from_status:
                        raise InvalidTransition(actual, to_status)
                    fields = ["status = %s", "status_updated_at = now()"]
                    params: list[Any] = [to_status]
                    if last_error is not None:
                        fields.append("last_error = %s")
                        params.append(last_error)
                    if bump_attempts:
                        fields.append("attempts = attempts + 1")
                    params.append(video_id)
                    cur.execute(
                        f"UPDATE videos SET {', '.join(fields)} WHERE id = %s",
                        params,
                    )
                    cur.execute(
                        "INSERT INTO status_log "
                        "(video_id, from_status, to_status, actor, detail) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (video_id, from_status, to_status, self.actor, detail),
                    )
        log.info(
            "transition video=%s %s -> %s actor=%s",
            video_id, from_status, to_status, self.actor,
        )


__all__ = [
    "STATUSES",
    "ALLOWED_TRANSITIONS",
    "InvalidTransition",
    "VideoNotFound",
    "Claimed",
    "StateMachine",
    "is_allowed",
    "validate",
]
