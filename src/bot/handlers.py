"""Команды бота: /status, /pause, /resume.

Хендлеры построены на aiogram 3.x. Логика, которую можно тестировать
без БД и без aiogram, вынесена в чистые функции (`format_status_text`,
`flip_pause`).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from src.bot.cards import send_topic_review_card
from src.core import costs, formats_store, settings_store
from src.core.config import get_settings
from src.core.db import connection

log = logging.getLogger(__name__)
router = Router(name="commands")


# ------------- чистые функции (тестируемые без aiogram/БД) ----------------

def format_status_text(counts: dict[str, int], errors: list[tuple[str, str]]) -> str:
    """Превращает агрегаты в человекочитаемый ответ /status."""
    lines = ["*Pipeline status*"]
    if not counts:
        lines.append("_no videos yet_")
    else:
        for status, n in counts.items():
            lines.append(f"• `{status}`: {n}")
    lines.append("")
    if errors:
        lines.append("*Recent errors*")
        for worker, err in errors[:5]:
            lines.append(f"• [{worker}] {err[:120]}")
    return "\n".join(lines)


def _is_owner_chat(chat_id: int | str | None) -> bool:
    s = get_settings()
    if not s.TELEGRAM_OWNER_CHAT_ID:
        return True  # dev-режим без owner — пускаем
    if chat_id is None:
        return False
    return str(chat_id) == str(s.TELEGRAM_OWNER_CHAT_ID)


def _is_owner(message: Message) -> bool:
    try:
        return _is_owner_chat(message.chat.id)
    except AttributeError:
        return False


# ------------- aiogram handlers ------------------------------------------

@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if not _is_owner(message):
        return
    counts: dict[str, int] = {}
    errors: list[tuple[str, str]] = []
    try:
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status::text, COUNT(*) FROM videos GROUP BY status"
                )
                counts = {row[0]: row[1] for row in cur.fetchall()}
                cur.execute(
                    "SELECT worker, error FROM errors "
                    "WHERE NOT resolved ORDER BY created_at DESC LIMIT 5"
                )
                errors = [(row[0], row[1]) for row in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001 — статус не должен ронять бота
        log.exception("/status DB error")
        await message.answer(f"DB error: {exc}")
        return
    await message.answer(
        format_status_text(counts, errors),
        parse_mode="Markdown",
    )


@router.message(Command("pause"))
async def cmd_pause(message: Message) -> None:
    if not _is_owner(message):
        return
    try:
        settings_store.pause()
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"failed to set pause: {exc}")
        return
    await message.answer("paused. workers will skip new claims.")


@router.message(Command("resume"))
async def cmd_resume(message: Message) -> None:
    if not _is_owner(message):
        return
    try:
        settings_store.resume()
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"failed to resume: {exc}")
        return
    await message.answer("resumed.")


# ------------- kill switches (/switches) ---------------------------------

CB_SWITCH = "sw"  # sw:<key>


def _switches_kb() -> InlineKeyboardMarkup:
    """Ряд кнопок под каждый switch — состояние читается динамически
    при рендере, не хранится в callback_data."""
    rows: list[list[InlineKeyboardButton]] = []
    for key, _label in settings_store.SWITCH_KEYS:
        on = not settings_store.get_flag(key, default=False)  # ON = не paused
        emoji = "🟢" if on else "🔴"
        rows.append([InlineKeyboardButton(
            text=f"{emoji} {key}",
            callback_data=f"{CB_SWITCH}:{key}",
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_switches_text() -> str:
    """Читаемый список переключателей с описаниями."""
    lines = ["*Kill switches*", ""]
    for key, label in settings_store.SWITCH_KEYS:
        paused = settings_store.get_flag(key, default=False)
        state = "🔴 OFF" if paused else "🟢 ON"
        lines.append(f"• `{key}` — {label}")
        lines.append(f"  {state}")
    lines.append("")
    lines.append("_Тап по кнопке ниже — переключение._")
    return "\n".join(lines)


@router.message(Command("switches"))
async def cmd_switches(message: Message) -> None:
    """Единый экран управления всеми kill-switches."""
    if not _is_owner(message):
        return
    try:
        text = _format_switches_text()
        kb = _switches_kb()
    except Exception as exc:  # noqa: BLE001
        log.exception("/switches failed")
        await message.answer(f"db error: {exc}")
        return
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)


@router.callback_query(F.data.startswith(f"{CB_SWITCH}:"))
async def cb_switch_toggle(cb: CallbackQuery) -> None:
    if not _is_owner_chat(cb.message.chat.id if cb.message else None):
        await cb.answer("denied", show_alert=True)
        return
    key = (cb.data or "").split(":", 1)[1] if ":" in (cb.data or "") else ""
    known = {k for k, _ in settings_store.SWITCH_KEYS}
    if key not in known:
        await cb.answer("unknown switch", show_alert=True)
        return
    try:
        new_paused = settings_store.toggle_switch(key)
    except Exception as exc:  # noqa: BLE001
        log.exception("toggle switch failed")
        await cb.answer(f"failed: {exc}", show_alert=True)
        return
    await cb.answer(f"{key} → {'OFF' if new_paused else 'ON'}")
    # Перерисовываем клавиатуру с новыми состояниями.
    if cb.message:
        try:
            await cb.message.edit_text(
                _format_switches_text(),
                parse_mode="Markdown",
                reply_markup=_switches_kb(),
            )
        except Exception:  # noqa: BLE001
            pass


# ------------- format toggles (/formats) ---------------------------------

CB_FORMAT = "fm"  # fm:<format>


def _formats_kb(rows: list[dict]) -> InlineKeyboardMarkup:
    """Ряд кнопок под каждый формат — тап тумблит killed."""
    kb_rows: list[list[InlineKeyboardButton]] = []
    for r in rows:
        emoji = "🔴 OFF" if r["killed"] else "🟢 ON"
        kb_rows.append([InlineKeyboardButton(
            text=f"{emoji} {r['format']} (w={r['weight']})",
            callback_data=f"{CB_FORMAT}:{r['format']}",
        )])
    return InlineKeyboardMarkup(inline_keyboard=kb_rows)


def _format_formats_text(rows: list[dict]) -> str:
    lines = ["*Форматы контента*", ""]
    if not rows:
        lines.append("_нет форматов в БД_")
        return "\n".join(lines)
    for r in rows:
        state = "🔴 отключён" if r["killed"] else "🟢 включён"
        lines.append(f"• `{r['format']}` — weight={r['weight']}, {state}")
    lines.append("")
    lines.append(
        "_Тап по кнопке — включить/выключить. Killed форматы "
        "topic_strategist исключает из scoring'а._"
    )
    return "\n".join(lines)


@router.message(Command("formats"))
async def cmd_formats(message: Message) -> None:
    """Управление форматами: on/off тумблер per format."""
    if not _is_owner(message):
        return
    try:
        rows = formats_store.list_formats()
    except Exception as exc:  # noqa: BLE001
        log.exception("/formats failed")
        await message.answer(f"db error: {exc}")
        return
    await message.answer(
        _format_formats_text(rows),
        parse_mode="Markdown",
        reply_markup=_formats_kb(rows),
    )


@router.callback_query(F.data.startswith(f"{CB_FORMAT}:"))
async def cb_format_toggle(cb: CallbackQuery) -> None:
    if not _is_owner_chat(cb.message.chat.id if cb.message else None):
        await cb.answer("denied", show_alert=True)
        return
    fmt = (cb.data or "").split(":", 1)[1] if ":" in (cb.data or "") else ""
    if not fmt:
        await cb.answer("bad format", show_alert=True)
        return
    try:
        new_killed = formats_store.toggle_killed(fmt)
    except ValueError:
        await cb.answer("unknown format", show_alert=True)
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("toggle format failed")
        await cb.answer(f"failed: {exc}", show_alert=True)
        return
    await cb.answer(f"{fmt} → {'OFF' if new_killed else 'ON'}")
    if cb.message:
        try:
            rows = formats_store.list_formats()
            await cb.message.edit_text(
                _format_formats_text(rows),
                parse_mode="Markdown",
                reply_markup=_formats_kb(rows),
            )
        except Exception:  # noqa: BLE001
            pass


@router.message(Command("ping"))
async def cmd_ping(message: Message) -> None:
    """Базовый healthcheck команды; полезно при ручной проверке."""
    if not _is_owner(message):
        return
    paused = "yes" if _safe_is_paused() else "no"
    await message.answer(f"pong — paused: {paused}")


@router.message(Command("budget"))
async def cmd_budget(message: Message) -> None:
    """Расход за месяц: токены Claude (in/out), ElevenLabs символы,
    Creatomate минуты — счётчики из settings (E2.1, E2.3, E2.5)."""
    if not _is_owner(message):
        return
    try:
        in_t = int(settings_store.get("tokens_claude_in", 0) or 0)
        out_t = int(settings_store.get("tokens_claude_out", 0) or 0)
        el = int(settings_store.get("elevenlabs_chars_used", 0) or 0)
        cm = float(settings_store.get("creatomate_minutes_used", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"db error: {exc}")
        return
    # rough cost estimate (claude-sonnet-4-5 $3/1M in, $15/1M out)
    cost_claude = (in_t * 3.0 + out_t * 15.0) / 1_000_000
    await message.answer(
        "💰 Budget month-to-date\n"
        f"• Claude tokens: in={in_t:,} / out={out_t:,}  ~${cost_claude:.2f}\n"
        f"• ElevenLabs chars: {el:,}\n"
        f"• Creatomate minutes: {cm:.1f}",
    )


@router.message(Command("footage"))
async def cmd_footage(message: Message) -> None:
    """Статистика банка footage: игры, клипы, использование, дыры."""
    if not _is_owner(message):
        return
    try:
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT game, COUNT(*), SUM(used_count), SUM(duration_sec) "
                    "FROM footage GROUP BY game ORDER BY game"
                )
                games = cur.fetchall()
                cur.execute(
                    "SELECT DISTINCT last_error FROM videos "
                    "WHERE status='failed' AND last_error LIKE 'footage_gap:%' "
                    "  AND status_updated_at >= now() - interval '14 days'"
                )
                gaps = [r[0] for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"db error: {exc}")
        return

    lines: list[str] = ["🎞 Footage bank"]
    if not games:
        lines.append("_empty_")
    else:
        for g, n, used, dur in games:
            dur_min = (float(dur or 0)) / 60
            lines.append(f"• `{g}`: {n} clips, used×{int(used or 0)}, {dur_min:.1f} min")
    if gaps:
        lines.append("")
        lines.append("📭 gaps:")
        for g in gaps[:5]:
            lines.append(f"  · {g[:120]}")
    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("costs"))
async def cmd_costs(message: Message) -> None:
    """Сводка расходов из таблицы costs (последние 7д/30д/всё время)."""
    if not _is_owner(message):
        return
    try:
        seven = costs.summary_window(7)
        total_7d = costs.grand_total_window(7)
        total_30d = costs.grand_total_window(30)
        total_all = costs.grand_total_window(None)
    except Exception as exc:  # noqa: BLE001
        log.exception("/costs failed")
        await message.answer(f"db error: {exc}")
        return
    await message.answer(
        costs.format_summary(seven, total_7d, total_30d, total_all),
    )


@router.message(Command("digest"))
async def cmd_digest(message: Message) -> None:
    """Принудительно собрать и отправить воскресный дайджест."""
    if not _is_owner(message):
        return
    try:
        from src.bot.digest import send_digest
        stats = send_digest(force=True)
        await message.answer(
            f"digest sent: topics={stats['topics']}, videos={stats['videos']}"
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("/digest failed")
        await message.answer(f"digest failed: {exc}")


@router.message(Command("audit"))
async def cmd_audit(message: Message) -> None:
    """/audit, /audit on, /audit off — YT-аудит флаг (E3.3)."""
    if not _is_owner(message):
        return
    parts = (message.text or "").split()
    arg = parts[1].lower() if len(parts) > 1 else None
    try:
        if arg in ("on", "true", "yes", "1"):
            settings_store.set_audit_passed(True)
            await message.answer(
                "✅ api_audit_passed = true — publisher will upload as public."
            )
        elif arg in ("off", "false", "no", "0"):
            settings_store.set_audit_passed(False)
            await message.answer(
                "🔒 api_audit_passed = false — publisher will upload as private."
            )
        else:
            cur = settings_store.is_audit_passed()
            n_priv = _count_private_published()
            await message.answer(
                f"api_audit_passed: {'true' if cur else 'false'}\n"
                f"private published (awaiting manual toggle): {n_priv}\n\n"
                f"Use /audit on or /audit off to change."
            )
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"failed: {exc}")


def _count_private_published() -> int:
    try:
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM videos "
                    "WHERE status = 'published' AND youtube_video_id IS NOT NULL"
                )
                return int(cur.fetchone()[0])
    except Exception:
        return 0


def private_published_reminder() -> str | None:
    """Текст для воскресного дайджеста: если api_audit_passed=false и
    есть published-видео — напоминание о ручном переключении в public.
    """
    try:
        if settings_store.is_audit_passed():
            return None
        n = _count_private_published()
    except Exception:
        return None
    if n <= 0:
        return None
    return (
        f"🔒 {n} private video(s) on the channel are still in private mode. "
        f"YouTube API audit has not been marked passed; switch them to "
        f"public manually in YouTube Studio, then run /audit on."
    )


def _safe_is_paused() -> bool:
    try:
        return settings_store.is_paused()
    except Exception:
        return False


# ============================================================
# /start, /help — пользовательский onboarding
# ============================================================

HELP_TEXT = (
    "*Content Factory bot*\n\n"
    "/start — запустить бота\n"
    "/costs — дашборд расходов\n"
    "/status — статус системы и очереди\n"
    "/topics — топ pending тем на одобрение\n"
    "/note <идея> — добавить ручную идею для видео\n"
    "/budget — счётчики токенов/символов\n"
    "/footage — банк AI-футажа\n"
    "/digest — собрать воскресный дайджест прямо сейчас\n"
    "/audit — пере-аудит публикации YT\n"
    "/pause /resume — приостановить/возобновить весь пайплайн\n"
    "/switches — точечные kill-switches (YT / дистрибуция / news)\n"
    "/formats — форматы контента on/off (short / long_gaming / …)\n"
    "/help — этот список"
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if not _is_owner(message):
        return
    await message.answer(
        "👋 Content Factory готов к работе.\n\n"
        "Нажми /help чтобы увидеть полный список команд, или сразу "
        "пиши /note <твоя идея> — добавлю в пайплайн.",
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    if not _is_owner(message):
        return
    await message.answer(HELP_TEXT, parse_mode="Markdown")


# ============================================================
# /topics — список pending_approval тем
# ============================================================

TOPICS_TOP_N = 10


@router.message(Command("topics"))
async def cmd_topics(message: Message) -> None:
    if not _is_owner(message):
        return
    try:
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT v.id::text, v.format::text, t.title, "
                    "       t.score, t.suggested_format::text "
                    "FROM videos v "
                    "JOIN topics t ON t.id = v.topic_id "
                    "WHERE v.status = 'pending_approval' "
                    "ORDER BY COALESCE(t.score, 0) DESC, v.created_at DESC "
                    "LIMIT %s",
                    (TOPICS_TOP_N,),
                )
                rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        log.exception("/topics failed")
        await message.answer(f"db error: {exc}")
        return
    if not rows:
        await message.answer("Нет тем в pending_approval — очередь пуста.")
        return
    lines = [f"🗂 Pending topics ({len(rows)}):", ""]
    for vid, fmt, title, score, sfmt in rows:
        score_s = f"{int(score)}" if score is not None else "?"
        lines.append(
            f"• [{score_s}] {fmt or sfmt or '?'} — {(title or '')[:80]}\n"
            f"   id: `{vid}`"
        )
    lines.append("")
    lines.append("Карточки на одобрение уже в чате (от Strategist).")
    await message.answer("\n".join(lines), parse_mode="Markdown")


# ============================================================
# /note — ручная идея
# ============================================================

# Стоп-слова для key-token extract — короткий список, не лезу в spaCy/NLTK.
_NOTE_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "with", "for", "of", "in", "on",
    "at", "by", "to", "from", "is", "are", "was", "were",
    "и", "в", "на", "под", "с", "из", "за", "до", "от", "по", "для", "не",
    "это", "как", "что", "чем", "какая", "какие", "какой",
    "купить", "найти", "сделать", "хочу", "нужно", "ищу",
}

_TOKEN_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9$]+", re.UNICODE)


def extract_keywords(note: str, *, min_len: int = 3, max_n: int = 10) -> list[str]:
    """Достать ключевые токены из заметки для match-поиска по topics.

    Чистая функция — тестируется без БД.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in _TOKEN_RE.findall(note or ""):
        tok = raw.lower()
        if len(tok) < min_len:
            continue
        if tok in _NOTE_STOPWORDS:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= max_n:
            break
    return out


def infer_format(note: str) -> str:
    """Простая эвристика выбора формата под ручную идею."""
    low = (note or "").lower()
    if "short" in low or "shorts" in low or "tiktok" in low or "reel" in low:
        return "short"
    if "bridge" in low:
        return "bridge"
    # PC-gaming/setup ниша: hardware/обзоры → long_setup по умолчанию.
    return "long_setup"


@router.message(Command("note"))
async def cmd_note(message: Message) -> None:
    if not _is_owner(message):
        return
    text = (message.text or "").strip()
    # Срезаем "/note " (или "/note@bot ")
    body = re.sub(r"^/note(@\w+)?\s*", "", text, count=1).strip()
    if not body:
        await message.answer(
            "Использование: `/note <текст идеи>`\n"
            "Например: `/note купить механическую клавиатуру под $100`",
            parse_mode="Markdown",
        )
        return
    try:
        result = _process_note(body)
    except Exception as exc:  # noqa: BLE001
        log.exception("/note failed")
        await message.answer(f"failed: {exc}")
        return

    if result["matches"]:
        lines = [
            f"🔎 Нашёл {len(result['matches'])} похожих тем в скрейпе. "
            f"Можешь продвинуть любую из них:",
            "",
        ]
        for m in result["matches"]:
            score_s = (f"{int(m['score'])}/100" if m["score"] is not None
                       else "no-score")
            lines.append(
                f"• [{score_s}] {m['title'][:100]}\n"
                f"   id: `{m['topic_id']}`"
            )
        lines.append("")
        lines.append("Кнопки одобрения ⤵")
        await message.answer("\n".join(lines), parse_mode="Markdown")
        # Отдельные карточки с кнопками на каждый матч
        for m in result["matches"]:
            await _send_match_card(message, m)
        return

    # No match → созданa новая тема + pending_approval видео
    await message.answer(
        f"✅ Идея добавлена в пайплайн как новая тема "
        f"(format={result['created_format']}, score={result['created_score']}).",
    )
    # Стандартная карточка темы с кнопками одобрения/отказа
    send_topic_review_card(
        topic_id=result["created_topic_id"],
        title=body,
        score=result["created_score"],
        reasoning="Ручная идея через /note (приоритет: высокий).",
        suggested_format=result["created_format"],
    )


# ----- внутренние helpers /note -----

CB_NOTE_MATCH_APPROVE = "nta"  # nta:<topic_id>


async def _send_match_card(message: Message, m: dict) -> None:
    """Карточка матча с кнопкой 'продвинуть' (создаст pending_approval video)."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Продвинуть",
            callback_data=f"{CB_NOTE_MATCH_APPROVE}:{m['topic_id']}",
        ),
    ]])
    score_s = (f"{int(m['score'])}/100" if m["score"] is not None
               else "no-score")
    await message.answer(
        f"[{score_s}] {m['title'][:200]}",
        reply_markup=kb,
    )


def _process_note(note: str) -> dict:
    """Записать заметку, поискать матчи, если нет — создать тему. Возвращает
    структуру с информацией для UX-ответа."""
    keywords = extract_keywords(note)
    matches = _search_topics_by_keywords(keywords) if keywords else []
    with connection() as conn:
        with conn.transaction():
            # 1. manual_topics — log записи
            matched_id = matches[0]["topic_id"] if matches else None
            created_topic_id = None
            created_format = None
            created_score = None
            if not matches:
                # 2a. создаём новую тему
                created_format = infer_format(note)
                created_score = 80.0
                created_topic_id = _create_manual_topic(
                    conn, note=note, fmt=created_format, score=created_score,
                )
                # 3. видео pending_approval с этим топиком
                _create_pending_video(
                    conn, topic_id=created_topic_id, fmt=created_format,
                )
            # Лог в manual_topics ВСЕГДА (и для matches и для нового)
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO manual_topics "
                    "(note, matched_topic_id, created_topic_id) "
                    "VALUES (%s, %s, %s)",
                    (note, matched_id, created_topic_id),
                )
    return {
        "matches": matches,
        "created_topic_id": created_topic_id,
        "created_format": created_format,
        "created_score": created_score,
    }


def _search_topics_by_keywords(keywords: list[str], *,
                               max_results: int = 5,
                               days: int = 14) -> list[dict]:
    """Найти темы за последние N дней с любым совпадением по title."""
    if not keywords:
        return []
    # ILIKE OR по всем kw — потом ранжируем по # совпадений в Python.
    placeholders = " OR ".join("title ILIKE %s" for _ in keywords)
    params: list = [f"%{k}%" for k in keywords]
    sql = (
        f"SELECT id::text, title, score, suggested_format::text "
        f"FROM topics "
        f"WHERE created_at >= now() - (%s::int || ' days')::interval "
        f"  AND ({placeholders}) "
        f"ORDER BY COALESCE(score, 0) DESC LIMIT 50"
    )
    params = [days, *params]
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    candidates = []
    for tid, title, score, sfmt in rows:
        low = (title or "").lower()
        hits = sum(1 for k in keywords if k in low)
        candidates.append({
            "topic_id": tid, "title": title, "score": score,
            "suggested_format": sfmt, "hits": hits,
        })
    # Ранжируем: больше hits → выше; tie-break по score.
    candidates.sort(
        key=lambda x: (x["hits"], float(x["score"] or 0)), reverse=True,
    )
    return candidates[:max_results]


def _create_manual_topic(conn, *, note: str, fmt: str, score: float) -> str:
    """Создать запись в topics с source='manual'."""
    import hashlib
    import time as _t
    dedup = "manual:" + hashlib.sha256(
        (note + str(_t.time())).encode()
    ).hexdigest()[:24]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO topics "
            "(source, title, score, score_reasoning, suggested_format, "
            " dedup_hash) "
            "VALUES ('manual', %s, %s, %s, %s::video_format, %s) "
            "RETURNING id::text",
            (note[:500], score, "Manual idea via /note (high priority).",
             fmt, dedup),
        )
        return cur.fetchone()[0]


def _create_pending_video(conn, *, topic_id: str, fmt: str) -> str | None:
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


@router.callback_query(F.data.startswith(f"{CB_NOTE_MATCH_APPROVE}:"))
async def note_match_approve(cb: CallbackQuery) -> None:
    if not _is_owner_chat(cb.message.chat.id if cb.message else None):
        await cb.answer("not owner", show_alert=True)
        return
    _, _, topic_id = (cb.data or "").partition(":")
    if not topic_id:
        await cb.answer("bad data")
        return
    try:
        video_id = _promote_topic_to_pending(topic_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("note_match_approve failed")
        await cb.answer(f"failed: {exc}", show_alert=True)
        return
    await cb.answer("queued for approval")
    if cb.message:
        await cb.message.edit_text(
            f"{cb.message.text}\n\n✅ Sent to approval queue ({video_id})",
            reply_markup=None,
        )


def _promote_topic_to_pending(topic_id: str) -> str:
    """Создать pending_approval video для существующего topic'а — если ещё нет."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text FROM videos "
                "WHERE topic_id = %s AND status IN ("
                "  'pending_approval','approved','scripted','footage_ready',"
                "  'voiced','assembled','packaged','ready_for_review',"
                "  'publish_queued','published'"
                ") ORDER BY created_at DESC LIMIT 1",
                (topic_id,),
            )
            row = cur.fetchone()
            if row:
                return row[0]
            cur.execute(
                "SELECT suggested_format::text FROM topics WHERE id = %s",
                (topic_id,),
            )
            srow = cur.fetchone()
        fmt = (srow[0] if srow and srow[0] else "long_setup")
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO videos (topic_id, format, status) "
                    "VALUES (%s, %s::video_format, 'pending_approval') "
                    "RETURNING id::text",
                    (topic_id, fmt),
                )
                return cur.fetchone()[0]


__all__ = [
    "router", "format_status_text",
    "extract_keywords", "infer_format",
    "HELP_TEXT", "CB_NOTE_MATCH_APPROVE",
]
