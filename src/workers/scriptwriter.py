"""Scriptwriter worker (спецификация 6.3 ТЗ).

Цикл:
  1. claim('approved') → одна запись videos
  2. собрать контекст: topic, game pool из footage, top-5 products (long only)
  3. LLM pass 1 — генерация скрипта (hook ≤15 сек, секции с [FOOTAGE:tag], CTA)
  4. LLM pass 2 — факт-чек собственного текста
  5. слить result в videos.script jsonb
  6. transition approved → scripted (или → failed если есть severity=high и
     wrong verdict — это значит модель явно соврала)

Длительность спикинга оценивается по формату:
  short        → 50  секунд тела + 10 сек hook/cta
  long_gaming  → 600 секунд
  long_setup   → 600 секунд
  bridge       → 180 секунд
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core import llm
from src.core.config import project_root
from src.core.db import connection
from src.core.state_machine import Claimed, StateMachine

log = logging.getLogger(__name__)

WORKER_NAME = "scriptwriter"

# Целевая длина озвучки (секунды) — из формата.
_TARGET_DURATION_SEC: dict[str, int] = {
    "short":       50,
    "long_gaming": 600,
    "long_setup":  600,
    "bridge":      180,
}


# ---------- репозитории ----------

@dataclass
class TopicData:
    id: str
    title: str
    url: str | None
    score_reasoning: str | None


def _fetch_topic(conn, topic_id: str) -> TopicData | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id::text, title, url, score_reasoning "
            "FROM topics WHERE id = %s",
            (topic_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return TopicData(id=row[0], title=row[1], url=row[2], score_reasoning=row[3])


def _fetch_video(conn, video_id: str) -> tuple[str, str] | None:
    """Возвращает (topic_id, format) для видео в статусе approved."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT topic_id::text, format::text FROM videos WHERE id = %s",
            (video_id,),
        )
        row = cur.fetchone()
    return (row[0], row[1]) if row else None


def _game_pool(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT game FROM footage ORDER BY game")
        return [r[0] for r in cur.fetchall()]


def _top_products(conn, *, limit: int = 5, category: str | None = None) -> list[dict[str, Any]]:
    """Топ-N активных продуктов с валидной аффилиатной ссылкой.

    Простейшая стратегия — по убыванию `commission_pct`, NULLS LAST. Опционально
    фильтр по категории (для setup-форматов можно сузить).
    """
    sql = (
        "SELECT id::text, name, category, affiliate_url, commission_pct, price_usd "
        "FROM products "
        "WHERE active AND link_ok "
        + ("AND category = %s " if category else "")
        + "ORDER BY commission_pct DESC NULLS LAST, name "
        + "LIMIT %s"
    )
    args: tuple = ((category, limit) if category else (limit,))
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return [
            {
                "id": r[0], "name": r[1], "category": r[2],
                "affiliate_url": r[3],
                "commission_pct": float(r[4]) if r[4] is not None else None,
                "price_usd": float(r[5]) if r[5] is not None else None,
            }
            for r in cur.fetchall()
        ]


def _save_script(conn, video_id: str, script: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE videos SET script = %s::jsonb, title = %s WHERE id = %s",
            (json.dumps(script), script.get("title"), video_id),
        )


# ---------- prompt assembly ----------

def _read_template(name: str) -> str:
    p = project_root() / "templates" / "prompts" / name
    return p.read_text(encoding="utf-8")


def _products_block(products: list[dict[str, Any]]) -> str:
    if not products:
        return "_No product integrations for this format._"
    lines = ["Use these specific products (do not invent others):"]
    for i, p in enumerate(products, 1):
        price = f"${p['price_usd']:.0f}" if p.get("price_usd") else "n/a"
        lines.append(
            f"{i}. id=`{p['id']}` — {p['name']} ({p['category']}, {price})"
        )
    return "\n".join(lines)


def _build_script_prompt(
    *, fmt: str, topic: TopicData, games: list[str], products: list[dict[str, Any]],
    edit_directive: str | None = None,
) -> str:
    target = _TARGET_DURATION_SEC.get(fmt, 60)
    is_long = fmt.startswith("long")
    template = _read_template("scriptwriter_script.md")
    products_rule = (
        "8. **Products** — naturally weave in the listed products. Reference "
        "them by their `id` in `products_used`. Do NOT mention products not "
        "listed below." if is_long else
        "8. **No products in this format.** Leave `products_used` as []."
    )
    base = template.format(
        format=fmt,
        topic_title=topic.title,
        topic_url=topic.url or "n/a",
        topic_reasoning=topic.score_reasoning or "—",
        game_pool=", ".join(games) if games else "(no footage yet)",
        target_duration_sec=target,
        products_block=_products_block(products) if is_long else "n/a",
        products_rule=products_rule,
    )
    # Жёсткое напоминание про scene_description: видели, что модель его
    # проглатывала. Дублируем в самом конце промпта.
    if uses_ai_footage(fmt):
        base += (
            "\n\n# CRITICAL — must include for THIS request\n"
            f"This is a `{fmt}` video. Every section in your JSON output MUST\n"
            "include a non-empty `scene_description` (≤200 chars). Without it\n"
            "the script will be rejected. `footage_tag` must still be set as\n"
            "a semantic hint."
        )
    if edit_directive:
        base += (
            "\n\n# OWNER OVERRIDE — TOP PRIORITY\n\n"
            f"{edit_directive.strip()}\n\n"
            "Follow this direction precisely. If it conflicts with other "
            "instructions above, the override wins."
        )
    return base


def _build_factcheck_prompt(script: dict[str, Any]) -> str:
    template = _read_template("scriptwriter_factcheck.md")
    return template.format(script_json=json.dumps(script, ensure_ascii=False, indent=2))


# ---------- AC: валидация структуры скрипта ----------

class InvalidScript(ValueError):
    pass


# Источник футажа по формату (раздел 2 ТЗ §3 после AL-001):
_AI_FOOTAGE_FORMATS = frozenset({"short", "long_setup"})
_BANK_FOOTAGE_FORMATS = frozenset({"long_gaming", "bridge"})


def uses_ai_footage(fmt: str) -> bool:
    return fmt in _AI_FOOTAGE_FORMATS


def _validate_script(script: Any, fmt: str) -> None:
    if not isinstance(script, dict):
        raise InvalidScript(f"script must be dict, got {type(script).__name__}")
    for key in ("title", "hook", "sections", "cta"):
        if key not in script:
            raise InvalidScript(f"missing key: {key}")
    if not isinstance(script["sections"], list) or not script["sections"]:
        raise InvalidScript("sections must be non-empty list")
    if len(script.get("title", "")) > 60:
        raise InvalidScript(f"title length {len(script['title'])} > 60")
    ai = uses_ai_footage(fmt)
    for i, s in enumerate(script["sections"]):
        if not isinstance(s, dict) or "voice" not in s or "footage_tag" not in s:
            raise InvalidScript(f"section[{i}] missing voice/footage_tag")
        if ai:
            desc = s.get("scene_description") or ""
            if not isinstance(desc, str) or not desc.strip():
                raise InvalidScript(
                    f"section[{i}] missing scene_description (required for {fmt})"
                )
            if len(desc) > 200:
                raise InvalidScript(
                    f"section[{i}] scene_description too long ({len(desc)} > 200)"
                )
    n = len(script["sections"])
    if fmt == "short" and not (1 <= n <= 3):
        raise InvalidScript(f"short format expects 1-3 sections, got {n}")
    if fmt.startswith("long") and not (4 <= n <= 7):
        raise InvalidScript(f"long format expects 4-7 sections, got {n}")


def _has_blocking_issue(factcheck: dict[str, Any]) -> tuple[bool, str | None]:
    """Есть ли issue severity=high + verdict=wrong → блокер для производства."""
    for issue in factcheck.get("issues") or []:
        if (
            issue.get("severity") == "high"
            and issue.get("verdict") == "wrong"
        ):
            return True, issue.get("claim") or issue.get("note") or "unknown"
    return False, None


# ---------- основной шаг ----------

def process_video(claimed: Claimed) -> str:
    """Один полный цикл для одной записи. Возвращает следующий статус."""
    video_id = claimed.video_id
    fmt = claimed.format or ""
    log.info("scriptwriter start video=%s format=%s", video_id, fmt)

    with connection() as conn:
        vmeta = _fetch_video(conn, video_id)
        if vmeta is None:
            raise RuntimeError(f"video {video_id} disappeared")
        topic_id, db_fmt = vmeta
        fmt = fmt or db_fmt
        topic = _fetch_topic(conn, topic_id) if topic_id else None
        games = _game_pool(conn)
        products = (
            _top_products(conn, limit=5,
                          category="peripheral" if fmt == "long_setup" else None)
            if fmt.startswith("long") else []
        )

    if topic is None:
        # без темы скрипт не пишем — fail
        StateMachine(WORKER_NAME).transition(
            video_id, "approved", "failed",
            last_error="no topic linked to video", detail="missing topic",
        )
        return "failed"

    # ---- Pass 1: script ----
    # Если в script jsonb уже сидит edit_directive от владельца (E3.1/E3.4 правка),
    # пробрасываем её в промпт; Scriptwriter обязан её учесть.
    existing = {}
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT script FROM videos WHERE id = %s", (video_id,))
            row = cur.fetchone()
            existing = row[0] if (row and row[0]) else {}
    edit_directive = existing.get("edit_directive") if isinstance(existing, dict) else None

    script_prompt = _build_script_prompt(
        fmt=fmt, topic=topic, games=games, products=products,
        edit_directive=edit_directive,
    )
    script = llm.complete_json(
        [{"role": "user", "content": script_prompt}],
        system=(
            "You write tight, factual scripts for a small but serious gaming "
            "channel. No filler, no emojis, no AI tells."
        ),
        max_tokens=4096,
        worker=WORKER_NAME,
        video_id=video_id,
    )
    _validate_script(script, fmt)

    # ---- Pass 2: fact-check ----
    fact_prompt = _build_factcheck_prompt(script)
    factcheck = llm.complete_json(
        [{"role": "user", "content": fact_prompt}],
        system="You are a careful fact-checker. Only flag verifiable claims.",
        max_tokens=2048,
        worker=WORKER_NAME,
        video_id=video_id,
    )
    if not isinstance(factcheck, dict):
        factcheck = {"facts_checked": False, "issues": []}

    blocking, claim = _has_blocking_issue(factcheck)
    script["facts_checked"] = bool(factcheck.get("facts_checked", True)) and not blocking
    script["fact_check"] = factcheck

    with connection() as conn:
        with conn.transaction():
            _save_script(conn, video_id, script)

    if blocking:
        StateMachine(WORKER_NAME).transition(
            video_id, "approved", "failed",
            last_error=f"factcheck blocker: {claim}",
            detail="high-severity wrong claim",
        )
        return "failed"

    sm = StateMachine(WORKER_NAME)
    sm.transition(
        video_id, "approved", "scripted",
        detail=f"sections={len(script['sections'])} fact_issues={len(factcheck.get('issues') or [])}",
    )
    # long_gaming / bridge не нуждаются в AI-генерации — переводим сразу
    # в footage_ready, чтобы voice мог взять видео в работу.
    if not uses_ai_footage(fmt):
        sm.transition(
            video_id, "scripted", "footage_ready",
            detail="bank-footage format passthrough",
        )
        return "footage_ready"
    return "scripted"


def run_once(*, limit: int = 1) -> int:
    sm = StateMachine(WORKER_NAME)
    claimed = sm.claim("approved", limit=limit)
    if not claimed:
        return 0
    processed = 0
    for c in claimed:
        try:
            process_video(c)
            processed += 1
        except Exception:
            log.exception("scriptwriter failed video=%s", c.video_id)
            # state-machine остаётся в approved — следующий цикл подхватит
            # (retry-стратегия — задача отдельного слоя E1.4 на сетевых вызовах
            # внутри complete_json; structural errors здесь оставим без auto-fail)
    return processed


__all__ = [
    "WORKER_NAME",
    "process_video",
    "run_once",
    "InvalidScript",
]
