"""Подключение к Postgres + лёгкая обёртка над пулом psycopg.

Пул создаётся лениво при первом обращении. В тестах модуль можно
заинжектить кастомный `connect()` через `set_connect_override()`.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool

from src.core.config import require

_pool: ConnectionPool | None = None
_connect_override: Callable[[], psycopg.Connection[Any]] | None = None


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        settings = require("epic1")
        _pool = ConnectionPool(settings.SUPABASE_DB_URL, min_size=1, max_size=5, open=True)
    return _pool


@contextlib.contextmanager
def connection() -> Iterator[psycopg.Connection[Any]]:
    if _connect_override is not None:
        conn = _connect_override()
        try:
            yield conn
        finally:
            conn.close()
        return
    with _get_pool().connection() as conn:
        yield conn


def set_connect_override(fn: Callable[[], psycopg.Connection[Any]] | None) -> None:
    """Для тестов: подменить источник соединений."""
    global _connect_override
    _connect_override = fn


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
