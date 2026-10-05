"""SEO Packager (спецификация 6.7 ТЗ).

Цикл — кастомный claim, чтобы не конкурировать с thumbnail:
  • short в assembled — берём
  • long в assembled с непустым thumbnail_r2_key — берём
  • long без thumbnail — пропускаем (ждём thumbnail)

Шаги:
  1. LLM генерит title (≤60), description body, tags, pinned comment
  2. chapters рассчитываются из длительностей секций voice'а
  3. link-validation: каждый products.affiliate_url → HEAD 200, битые
     помечаются link_ok=false и исключаются из description
  4. description = body + chapters + "Gear in this video:" + links + FTC
  5. save seo jsonb, title; transition assembled → packaged → ready_for_review
  6. push карточку владельцу в Telegram с превью (signed URL, TTL 7 дней)
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from src.bot.alerts import send_alert
from src.core import llm, r2
from src.core.config import project_root, require, get_settings
from src.core.db import connection
from src.core.state_machine import StateMachine

log = logging.getLogger(__name__)

WORKER_NAME = "seo_packager"

FTC_LINE = (
    "Affiliate disclosure: links above are affiliate links. As an affiliate, "
    "we earn from qualifying purchases — at no extra cost to you."
)

# AL-001: short и long_setup используют AI-генерированные визуалы.
AI_DISCLOSURE_LINE = (
    "Visuals in this video are AI-generated (Kling). Voiceover is "
    "cloned via ElevenLabs from a single donor. Editorial — humans."
)
_AI_FORMATS_FOR_DISCLOSURE = frozenset({"short", "long_setup"})


def needs_ai_disclosure(fmt: str) -> bool:
    return fmt in _AI_FORMATS_FOR_DISCLOSURE


# --------- claim / persistence ---------

def _claim_assembled() -> tuple[str, str, str, dict[str, Any], str | None] | None:
    """FOR UPDATE SKIP LOCKED.
       Возвращает (id, format, title, script, voice_r2_key)."""
    sql = (
        "SELECT id::text, format::text, COALESCE(title, ''), script, voice_r2_key "
        "FROM videos "
        "WHERE status = 'assembled' "
        "  AND (format = 'short' OR thumbnail_r2_key IS NOT NULL) "
        "ORDER BY status_updated_at "
        "FOR UPDATE SKIP LOCKED LIMIT 1"
    )
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
    if not row:
        return None
    return row[0], row[1], row[2], row[3] or {}, row[4]


def _save_seo(conn, video_id: str, title: str, seo: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE videos SET seo = %s::jsonb, title = %s WHERE id = %s",
            (json.dumps(seo), title, video_id),
        )


def _topic_products(conn, video_id: str, *, limit: int = 5,
                    category_hint: str | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT id::text, name, category, affiliate_url, price_usd "
        "FROM products WHERE active "
        + ("AND category = %s " if category_hint else "")
        + "ORDER BY commission_pct DESC NULLS LAST, name LIMIT %s"
    )
    with conn.cursor() as cur:
        if category_hint:
            cur.execute(sql, (category_hint, limit))
        else:
            cur.execute(sql, (limit,))
        return [
            {"id": r[0], "name": r[1], "category": r[2],
             "affiliate_url": r[3], "price_usd": float(r[4]) if r[4] else None}
            for r in cur.fetchall()
        ]


def _mark_link_status(conn, product_id: str, ok: bool) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE products SET link_ok = %s, last_link_check = now() WHERE id = %s",
            (ok, product_id),
        )


# --------- link validation ---------

def validate_link(url: str, *, timeout: float = 10.0) -> bool:
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as c:
            r = c.head(url)
            if r.status_code in (405, 403):
                r = c.get(url)
            return 200 <= r.status_code < 400
    except httpx.HTTPError:
        return False


# --------- chapters ---------

def _format_ts(sec: float) -> str:
    sec = int(round(sec))
    m, s = divmod(sec, 60)
    return f"{m}:{s:02d}"


def _build_chapters(script: dict[str, Any]) -> list[dict[str, str]]:
    chapters: list[dict[str, str]] = []
    t = 0.0
    # вступление = hook
    chapters.append({"time": _format_ts(0), "title": "Intro"})
    t += float(script.get("hook_duration_sec") or 0)
    for s in script.get("sections") or []:
        title = (s.get("id") or "Section").replace("-", " ").title()
        chapters.append({"time": _format_ts(t), "title": title})
        t += float(s.get("voice_duration_sec") or 0)
    return chapters


# --------- description assembly ---------

def _format_chapters(chapters: list[dict[str, str]]) -> str:
    lines = ["Chapters:"]
    for c in chapters:
        lines.append(f"{c['time']} {c['title']}")
    return "\n".join(lines)


def _format_gear(products: list[dict[str, Any]]) -> str:
    if not products:
        return ""
    lines = ["Gear in this video:"]
    for p in products:
        price = f"  (~${p['price_usd']:.0f})" if p.get("price_usd") else ""
        lines.append(f"• {p['name']}{price}: {p['affiliate_url']}")
    return "\n".join(lines)


def _full_description(body: str, chapters: list[dict[str, str]],
                      products: list[dict[str, Any]],
                      ai_disclosure: bool = False) -> str:
    parts: list[str] = [body.strip(), "", _format_chapters(chapters)]
    gear = _format_gear(products)
    if gear:
        parts.extend(["", gear, "", FTC_LINE])
    if ai_disclosure:
        parts.extend(["", AI_DISCLOSURE_LINE])
    return "\n".join(parts).strip() + "\n"


# --------- основной шаг ---------

def _llm_seo(script: dict[str, Any]) -> dict[str, Any]:
    template = (project_root() / "templates" / "prompts" / "seo.md").read_text()
    prompt = template.format(
        script_json=json.dumps(script, ensure_ascii=False, indent=2),
    )
    obj = llm.complete_json(
        [{"role": "user", "content": prompt}],
        system="You write YouTube packaging copy. No AI tells, no emojis.",
        max_tokens=2048,
        worker=WORKER_NAME,
    )
    if not isinstance(obj, dict):
        raise RuntimeError(f"seo LLM returned non-dict: {type(obj)}")
    return obj


def process_one(video_id: str, fmt: str, title_in: str,
                script: dict[str, Any], voice_key: str | None) -> str:
    log.info("seo start video=%s format=%s", video_id, fmt)
    require("epic2")

    # 1) LLM packaging
    obj = _llm_seo(script)
    title = (obj.get("title") or title_in or "Untitled")[:60]
    body = obj.get("description_body") or ""
    tags = [t for t in (obj.get("tags") or []) if isinstance(t, str)][:18]
    pinned = obj.get("pinned_comment") or ""

    # 2) chapters
    chapters = _build_chapters(script)

    # 3) products + link validation
    category_hint = "peripheral" if fmt == "long_setup" else None
    with connection() as conn:
        all_products = _topic_products(conn, video_id,
                                       category_hint=category_hint, limit=5)
    valid_products: list[dict[str, Any]] = []
    for p in all_products:
        ok = validate_link(p["affiliate_url"])
        with connection() as conn:
            with conn.transaction():
                _mark_link_status(conn, p["id"], ok)
        if ok:
            valid_products.append(p)
        else:
            log.warning("seo skip broken link %s for video=%s", p["affiliate_url"], video_id)

    # шорты не показывают продукты в description (нет места)
    if fmt == "short":
        valid_products = []

    ai_disclosure = needs_ai_disclosure(fmt)
    description = _full_description(body, chapters, valid_products,
                                    ai_disclosure=ai_disclosure)

    seo = {
        "title": title,
        "description": description,
        "ai_disclosure": ai_disclosure,
        "description_body": body,
        "tags": tags,
        "chapters": chapters,
        "pinned_comment": pinned,
        "products": [
            {"id": p["id"], "name": p["name"], "url": p["affiliate_url"]}
            for p in valid_products
        ],
        "ftc_disclosure": FTC_LINE,
    }

    with connection() as conn:
        with conn.transaction():
            _save_seo(conn, video_id, title, seo)

    sm = StateMachine(WORKER_NAME)
    sm.transition(video_id, "assembled", "packaged",
                  detail=f"tags={len(tags)} products={len(valid_products)}")
    sm.transition(video_id, "packaged", "ready_for_review",
                  detail="auto from seo_packager")

    # 4) Telegram-карточка
    _push_review_card(video_id, title, description)
    return "ready_for_review"


# --------- Telegram card ---------

def _push_review_card(video_id: str, title: str, description: str) -> None:
    s = get_settings()
    if not s.TELEGRAM_BOT_TOKEN or not s.TELEGRAM_OWNER_CHAT_ID:
        return
    from src.bot.cards import send_video_review_card

    render_key = None
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT render_r2_key FROM videos WHERE id = %s", (video_id,))
            row = cur.fetchone()
    if row and row[0]:
        render_key = row[0]
    # Защита: signed URL на отсутствующий объект отдаёт NoSuchKey при клике.
    # Подтверждаем head_object перед подстановкой превью.
    preview_url = None
    if render_key:
        try:
            if r2.object_exists(s.R2_BUCKET_RENDERS, render_key):
                preview_url = r2.signed_url(s.R2_BUCKET_RENDERS, render_key)
            else:
                log.warning("review card preview skipped: %s missing in R2",
                            render_key)
        except Exception:
            log.warning("review card preview check failed", exc_info=True)
    short_desc = description.split("\n\n", 1)[0][:400]
    try:
        send_video_review_card(
            video_id=video_id, title=title,
            short_description=short_desc, preview_url=preview_url,
        )
    except Exception:
        log.warning("review card send failed", exc_info=True)


def run_once() -> int:
    pick = _claim_assembled()
    if not pick:
        return 0
    video_id, fmt, title, script, voice_key = pick
    try:
        process_one(video_id, fmt, title, script, voice_key)
        return 1
    except Exception:
        log.exception("seo failed video=%s", video_id)
        return 0


__all__ = [
    "WORKER_NAME",
    "FTC_LINE",
    "validate_link",
    "process_one",
    "run_once",
]
