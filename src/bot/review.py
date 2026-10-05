"""Inline-кнопки для карточек ready_for_review (E3.1) и pending_approval (E3.4).

Архитектурно: вызов кнопки → CallbackQuery → handler меняет статус через
StateMachine. Для «правка» используем aiogram FSM (MemoryStorage) — следующий
текстовый ответ владельца сохраняется в script.edit_directive / topic edit
note и видео/тема возвращается в approved.
"""

from __future__ import annotations

import json
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import datetime as dt

from src.bot.cards import (
    CB_TOPIC_APPROVE, CB_TOPIC_EDIT, CB_TOPIC_REJECT,
    CB_VIDEO_APPROVE, CB_VIDEO_EDIT, CB_VIDEO_REJECT,
    parse_callback,
)
from src.bot.handlers import _is_owner_chat
from src.core import slots
from src.core.db import connection
from src.core.state_machine import InvalidTransition, StateMachine

log = logging.getLogger(__name__)
router = Router(name="review")


class EditFlow(StatesGroup):
    awaiting_video_edit = State()
    awaiting_topic_edit = State()


# ---------- video review ----------

@router.callback_query(F.data.startswith(f"{CB_VIDEO_APPROVE}:"))
async def video_approve(cb: CallbackQuery) -> None:
    if not _is_owner_chat(cb.message.chat.id if cb.message else None):
        await cb.answer("not owner", show_alert=True)
        return
    parsed = parse_callback(cb.data)
    if not parsed:
        await cb.answer("bad data")
        return
    video_id = parsed[1]
    # Назначить ближайший разрешённый слот формата (E3.2: шорты пн/ср/пт/сб 17 UTC,
    # длинные вт/пт 16 UTC).
    try:
        fmt = _video_format(video_id)
        slot = slots.next_slot(dt.datetime.now(dt.timezone.utc), fmt)
        _set_publish_slot(video_id, slot)
        StateMachine("owner").transition(
            video_id, "ready_for_review", "publish_queued",
            detail=f"approved by owner; slot={slot.isoformat()}",
        )
    except InvalidTransition as exc:
        await cb.answer(f"already moved: {exc}", show_alert=True)
        return
    await cb.answer(f"queued for {slot:%Y-%m-%d %H:%M} UTC")
    if cb.message:
        await cb.message.edit_text(
            f"{cb.message.text}\n\n✅ Approved → publish_queued · "
            f"slot {slot:%a %Y-%m-%d %H:%M} UTC",
            reply_markup=None,
        )


def _video_format(video_id: str) -> str:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT format::text FROM videos WHERE id = %s", (video_id,))
            row = cur.fetchone()
    if not row:
        raise RuntimeError(f"video {video_id} not found")
    return row[0]


def _set_publish_slot(video_id: str, slot: dt.datetime) -> None:
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE videos SET scheduled_publish_at = %s WHERE id = %s",
                    (slot, video_id),
                )


@router.callback_query(F.data.startswith(f"{CB_VIDEO_REJECT}:"))
async def video_reject(cb: CallbackQuery) -> None:
    if not _is_owner_chat(cb.message.chat.id if cb.message else None):
        await cb.answer("not owner", show_alert=True)
        return
    parsed = parse_callback(cb.data)
    if not parsed:
        await cb.answer("bad data")
        return
    video_id = parsed[1]
    try:
        StateMachine("owner").transition(
            video_id, "ready_for_review", "rejected",
            detail="rejected by owner",
        )
    except InvalidTransition as exc:
        await cb.answer(f"already moved: {exc}", show_alert=True)
        return
    await cb.answer("rejected")
    if cb.message:
        await cb.message.edit_text(
            f"{cb.message.text}\n\n❌ Rejected",
            reply_markup=None,
        )


@router.callback_query(F.data.startswith(f"{CB_VIDEO_EDIT}:"))
async def video_edit_start(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_owner_chat(cb.message.chat.id if cb.message else None):
        await cb.answer("not owner", show_alert=True)
        return
    parsed = parse_callback(cb.data)
    if not parsed:
        await cb.answer("bad data")
        return
    video_id = parsed[1]
    await state.set_state(EditFlow.awaiting_video_edit)
    await state.update_data(video_id=video_id)
    await cb.answer("send your edit instructions")
    if cb.message:
        await cb.message.reply(
            "✏️ Send a short directive — Scriptwriter will rewrite "
            f"video `{video_id}` with it.",
            parse_mode="Markdown",
        )


@router.message(EditFlow.awaiting_video_edit)
async def video_edit_apply(message: Message, state: FSMContext) -> None:
    if not _is_owner_chat(message.chat.id):
        return
    data = await state.get_data()
    video_id = data.get("video_id")
    directive = (message.text or "").strip()
    if not video_id or not directive:
        await state.clear()
        await message.answer("aborted")
        return
    try:
        _apply_video_edit_directive(video_id, directive)
        StateMachine("owner").transition(
            video_id, "ready_for_review", "approved",
            detail=f"owner edit: {directive[:80]}",
        )
    except InvalidTransition as exc:
        await message.answer(f"already moved: {exc}")
    except Exception as exc:  # noqa: BLE001
        log.exception("video edit apply failed")
        await message.answer(f"failed: {exc}")
    else:
        await message.answer(
            f"✏️ rewritten with: «{directive[:120]}» — sent to Scriptwriter",
        )
    finally:
        await state.clear()


def _apply_video_edit_directive(video_id: str, directive: str) -> None:
    """Положить директиву в script.edit_directive — Scriptwriter учтёт."""
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE videos "
                    "SET script = COALESCE(script, '{}'::jsonb) "
                    "         || jsonb_build_object('edit_directive', %s::text) "
                    "WHERE id = %s",
                    (directive, video_id),
                )


# ---------- topic review (E3.4) ----------

@router.callback_query(F.data.startswith(f"{CB_TOPIC_APPROVE}:"))
async def topic_approve(cb: CallbackQuery) -> None:
    if not _is_owner_chat(cb.message.chat.id if cb.message else None):
        await cb.answer("not owner", show_alert=True)
        return
    parsed = parse_callback(cb.data)
    if not parsed:
        await cb.answer("bad data")
        return
    topic_id = parsed[1]
    video_id = _find_video_for_topic(topic_id)
    if not video_id:
        await cb.answer("no video for topic", show_alert=True)
        return
    try:
        StateMachine("owner").transition(
            video_id, "pending_approval", "approved",
            detail="approved by owner",
        )
    except InvalidTransition as exc:
        await cb.answer(f"already moved: {exc}", show_alert=True)
        return
    await cb.answer("approved")
    if cb.message:
        await cb.message.edit_text(
            f"{cb.message.text}\n\n✅ Approved → scriptwriter",
            reply_markup=None,
        )


@router.callback_query(F.data.startswith(f"{CB_TOPIC_REJECT}:"))
async def topic_reject(cb: CallbackQuery) -> None:
    if not _is_owner_chat(cb.message.chat.id if cb.message else None):
        await cb.answer("not owner", show_alert=True)
        return
    parsed = parse_callback(cb.data)
    if not parsed:
        await cb.answer("bad data")
        return
    topic_id = parsed[1]
    video_id = _find_video_for_topic(topic_id)
    if not video_id:
        await cb.answer("no video", show_alert=True)
        return
    try:
        StateMachine("owner").transition(
            video_id, "pending_approval", "rejected",
            detail="rejected by owner",
        )
    except InvalidTransition as exc:
        await cb.answer(f"already moved: {exc}", show_alert=True)
        return
    await cb.answer("rejected")
    if cb.message:
        await cb.message.edit_text(
            f"{cb.message.text}\n\n❌ Rejected",
            reply_markup=None,
        )


@router.callback_query(F.data.startswith(f"{CB_TOPIC_EDIT}:"))
async def topic_edit_start(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_owner_chat(cb.message.chat.id if cb.message else None):
        await cb.answer("not owner", show_alert=True)
        return
    parsed = parse_callback(cb.data)
    if not parsed:
        await cb.answer("bad data")
        return
    topic_id = parsed[1]
    await state.set_state(EditFlow.awaiting_topic_edit)
    await state.update_data(topic_id=topic_id)
    await cb.answer("send your edit instructions")
    if cb.message:
        await cb.message.reply(
            "✏️ Send a short directive — Scriptwriter will write the script "
            "with this owner-direction.",
        )


@router.message(EditFlow.awaiting_topic_edit)
async def topic_edit_apply(message: Message, state: FSMContext) -> None:
    if not _is_owner_chat(message.chat.id):
        return
    data = await state.get_data()
    topic_id = data.get("topic_id")
    directive = (message.text or "").strip()
    if not topic_id or not directive:
        await state.clear()
        await message.answer("aborted")
        return
    video_id = _find_video_for_topic(topic_id)
    if not video_id:
        await state.clear()
        await message.answer("no video for topic")
        return
    try:
        _seed_script_with_directive(video_id, directive)
        StateMachine("owner").transition(
            video_id, "pending_approval", "approved",
            detail=f"owner edit: {directive[:80]}",
        )
    except InvalidTransition as exc:
        await message.answer(f"already moved: {exc}")
    except Exception as exc:  # noqa: BLE001
        log.exception("topic edit apply failed")
        await message.answer(f"failed: {exc}")
    else:
        await message.answer(
            f"✏️ noted: «{directive[:120]}» — Scriptwriter will use it",
        )
    finally:
        await state.clear()


def _find_video_for_topic(topic_id: str) -> str | None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text FROM videos "
                "WHERE topic_id = %s AND status = 'pending_approval' "
                "ORDER BY created_at DESC LIMIT 1",
                (topic_id,),
            )
            row = cur.fetchone()
    return row[0] if row else None


def _seed_script_with_directive(video_id: str, directive: str) -> None:
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE videos "
                    "SET script = COALESCE(script, '{}'::jsonb) "
                    "         || jsonb_build_object('edit_directive', %s::text) "
                    "WHERE id = %s",
                    (directive, video_id),
                )


__all__ = ["router", "EditFlow"]
