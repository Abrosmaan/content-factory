"""Topic Strategist (спецификация 6.2 ТЗ).

cron вс 06:00 UTC:
  1. собрать topics за 7 дней без score
  2. game pool из footage + format_weights из БД
  3. ОДИН батч-вызов Claude → score 0-100 + reasoning + suggested_format
  4. сохранить score обратно в topics
  5. топ-N → videos(pending_approval); карточки тем в Telegram

`killed_formats` (format_weights.killed=true) исключаются из выбора.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from src.bot.cards import send_topic_review_card
from src.core import llm
from src.core.config import project_root
from src.core.db import connection

log = logging.getLogger(__name__)

WORKER_NAME = "topic_strategist"
DEFAULT_TOP_N = 10
DAYS_LOOKBACK = 7
CHUNK_SIZE = 50


@dataclass
class TopicRow:
    id: str
    source: str
    title: str
    url: str | None
    payload: dict


def _fetch_unscored(conn, *, days: int) -> list[TopicRow]:
    sql = (
        "SELECT id::text, source, title, url, raw_payload "
        "FROM topics "
        "WHERE score IS NULL "
        "  AND created_at >= now() - (%s::int || ' days')::interval "
        "ORDER BY created_at DESC"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (days,))
        return [
            TopicRow(
                id=r[0], source=r[1], title=r[2], url=r[3], payload=r[4] or {},
            )
            for r in cur.fetchall()
        ]


def _game_pool(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT game FROM footage ORDER BY game")
        return [r[0] for r in cur.fetchall()]


def _format_weights(conn) -> tuple[dict[str, float], list[str]]:
    """Возвращает (weights dict, killed list)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT format::text, weight, killed FROM format_weights"
        )
        weights: dict[str, float] = {}
        killed: list[str] = []
        for fmt, w, k in cur.fetchall():
            weights[fmt] = float(w)
            if k:
                killed.append(fmt)
    return weights, killed


def _save_score(conn, topic_id: str, score: float, reasoning: str,
                suggested_format: str | None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE topics "
            "SET score = %s, score_reasoning = %s, suggested_format = %s::video_format "
            "WHERE id = %s",
            (score, reasoning, suggested_format, topic_id),
        )


def _create_pending_video(conn, *, topic_id: str, fmt: str) -> str | None:
    """Создаём pending_approval запись, если ещё не создавали для этой темы."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO videos (topic_id, format, status) "
            "VALUES (%s, %s::video_format, 'pending_approval') "
            "ON CONFLICT DO NOTHING "
            "RETURNING id::text",
            (topic_id, fmt),
        )
        row = cur.fetchone()
    return row[0] if row else None


# --------- LLM ---------

def _build_prompt(topics: list[TopicRow], games: list[str],
                  weights: dict[str, float], killed: list[str]) -> str:
    template = (project_root() / "templates" / "prompts" / "strategist.md").read_text()
    compact_topics = [
        {
            "topic_id": t.id,
            "source": t.source,
            "title": t.title,
            "url": t.url,
            "snippet": (t.payload.get("summary") or t.payload.get("selftext")
                        or "")[:280],
        }
        for t in topics
    ]
    return template.format(
        game_pool=", ".join(games) if games else "(no footage yet)",
        format_weights=json.dumps(weights),
        killed_formats=", ".join(killed) if killed else "(none)",
        topics_json=json.dumps(compact_topics, ensure_ascii=False),
    )


def _score_with_llm(topics: list[TopicRow]) -> list[dict[str, Any]]:
    with connection() as conn:
        games = _game_pool(conn)
        weights, killed = _format_weights(conn)
    all_scored: list[dict[str, Any]] = []
    total_chunks = (len(topics) + CHUNK_SIZE - 1) // CHUNK_SIZE
    for i in range(0, len(topics), CHUNK_SIZE):
        chunk = topics[i:i + CHUNK_SIZE]
        idx = i // CHUNK_SIZE + 1
        prompt = _build_prompt(chunk, games, weights, killed)
        try:
            obj = llm.complete_json(
                [{"role": "user", "content": prompt}],
                system=(
                    "You are the strategist for a serious gaming channel. "
                    "Be tough — most topics aren't worth a video."
                ),
                max_tokens=8000,
                worker=WORKER_NAME,
            )
        except Exception:
            log.warning("strategist chunk %d/%d failed", idx, total_chunks,
                        exc_info=True)
            continue
        if not isinstance(obj, dict):
            log.warning("strategist chunk %d/%d returned non-dict: %s",
                        idx, total_chunks, type(obj))
            continue
        scored = obj.get("scored") or []
        if not isinstance(scored, list):
            log.warning("strategist chunk %d/%d 'scored' not a list", idx,
                        total_chunks)
            continue
        log.info("strategist chunk %d/%d scored=%d", idx, total_chunks,
                 len(scored))
        all_scored.extend(scored)
    return all_scored


def run_once(*, top_n: int = DEFAULT_TOP_N) -> dict[str, int]:
    """Полный цикл. Возвращает счётчики."""
    stats = {"unscored": 0, "scored": 0, "pending": 0}
    with connection() as conn:
        topics = _fetch_unscored(conn, days=DAYS_LOOKBACK)
    stats["unscored"] = len(topics)
    if not topics:
        log.info("strategist: nothing to score")
        return stats

    log.info("strategist: scoring %d topics", len(topics))
    scored = _score_with_llm(topics)

    # Сохраняем score в topics
    by_id: dict[str, TopicRow] = {t.id: t for t in topics}
    valid: list[dict[str, Any]] = []
    with connection() as conn:
        with conn.transaction():
            for item in scored:
                tid = item.get("topic_id")
                row = by_id.get(tid) if tid else None
                if not row:
                    continue
                try:
                    score = float(item.get("score", 0))
                except (TypeError, ValueError):
                    continue
                reasoning = (item.get("reasoning") or "").strip()
                fmt = item.get("suggested_format")
                if fmt not in ("short", "long_gaming", "long_setup", "bridge"):
                    fmt = None
                _save_score(conn, tid, score, reasoning, fmt)
                stats["scored"] += 1
                valid.append({"topic": row, "score": score, "fmt": fmt,
                              "reasoning": reasoning})

    # Топ-N → pending_approval + карточки
    valid.sort(key=lambda x: x["score"], reverse=True)
    picked = valid[:top_n]
    with connection() as conn:
        with conn.transaction():
            for x in picked:
                fmt = x["fmt"] or "short"
                vid = _create_pending_video(conn, topic_id=x["topic"].id, fmt=fmt)
                if vid is None:
                    continue
                stats["pending"] += 1

    # Карточки — после COMMIT, чтобы owner мог увидеть pending_approval запись
    for x in picked:
        if stats["pending"] == 0:
            break
        try:
            send_topic_review_card(
                topic_id=x["topic"].id,
                title=x["topic"].title,
                score=x["score"],
                reasoning=x["reasoning"],
                suggested_format=x["fmt"],
            )
        except Exception:
            log.warning("topic card send failed", exc_info=True)

    log.info("strategist done: %s", stats)
    return stats


__all__ = ["WORKER_NAME", "run_once"]
