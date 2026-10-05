"""CRUD format_weights + kill-switch (killed=true) для UI /formats.

Топик-стратег читает format_weights в topic_strategist._format_weights
и уже исключает killed формы. Нам нужен UI-слой для тумблера + чтения.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.db import connection

log = logging.getLogger(__name__)


def list_formats() -> list[dict[str, Any]]:
    """Все форматы + их weight + killed-флаг. Стабильный порядок."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT format::text, weight::float, killed "
                "FROM format_weights ORDER BY format"
            )
            return [
                {"format": row[0], "weight": row[1], "killed": row[2]}
                for row in cur.fetchall()
            ]


def toggle_killed(fmt: str) -> bool:
    """Флип killed для формата. Возвращает новое состояние
    (True = killed, False = enabled). Raises если формат неизвестен."""
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT killed FROM format_weights WHERE format = %s",
                    (fmt,),
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError(f"unknown format: {fmt}")
                new_killed = not bool(row[0])
                cur.execute(
                    "UPDATE format_weights SET killed = %s "
                    "WHERE format = %s",
                    (new_killed, fmt),
                )
                return new_killed


def set_killed(fmt: str, killed: bool) -> None:
    """Явное выставление killed без чтения. Идемпотентно."""
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE format_weights SET killed = %s "
                    "WHERE format = %s",
                    (bool(killed), fmt),
                )
                if cur.rowcount == 0:
                    raise ValueError(f"unknown format: {fmt}")


__all__ = ["list_formats", "toggle_killed", "set_killed"]
