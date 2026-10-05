"""Assembler worker (спецификация 6.5 ТЗ).

Цикл:
  1. claim('voiced')
  2. подобрать клипы из footage по маркерам секций и игре темы:
     приоритет — наименее использованные (used_count ASC); инкремент used_count
  3. нет подходящих клипов → failed с last_error='footage_gap: <гэп>' + alert
  4. собрать modifications для Creatomate-шаблона:
     - footage_*.source — signed URL клипов из R2 (raw_footage bucket)
     - footage_*.duration — длительность по секциям из voice
     - voice.source / voice.duration — финальный mp3 из R2_BUCKET_RENDERS
     - subtitles.text — конкатенация секций
     - music.source — пока опционально (поле music/ в R2 заливает владелец)
  5. render через Creatomate (poll до succeeded)
  6. скачать готовый mp4 и залить в R2_BUCKET_RENDERS под renders/<uuid>.mp4
  7. update render_r2_key + transition voiced → assembled

Архитектура (раздел 2 ТЗ): только собственный банк footage; никаких
внешних видео-URL.
"""

from __future__ import annotations

import logging
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from src.bot.alerts import send_alert
from src.core import creatomate, r2
from src.core.config import require
from src.core.db import connection
from src.core.state_machine import Claimed, StateMachine

log = logging.getLogger(__name__)

WORKER_NAME = "assembler"

# Универсальные теги (подходят к любой теме). Используются как fallback.
_UNIVERSAL_TAGS = frozenset({"scenic", "b-roll", "transition", "intro", "outro"})

# Источник футажа по формату (раздел 2 ТЗ §3 после AL-001):
_AI_FOOTAGE_FORMATS = frozenset({"short", "long_setup"})


def uses_generated_clips(fmt: str) -> bool:
    return fmt in _AI_FOOTAGE_FORMATS


# --------- репозитории ---------

@dataclass
class Footage:
    id: str
    r2_key: str
    game: str
    tags: list[str]
    duration_sec: float


def _load_video(conn, video_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT v.format::text, v.script, v.voice_r2_key, t.title "
            "FROM videos v LEFT JOIN topics t ON t.id = v.topic_id "
            "WHERE v.id = %s",
            (video_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "format": row[0], "script": row[1] or {},
        "voice_r2_key": row[2], "topic_title": row[3],
    }


def _topic_game(conn, video_id: str) -> str | None:
    """Достать игру темы из topics.raw_payload / dedup. Не критично —
    если игры нет, fallback на любую."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT t.raw_payload "
            "FROM videos v JOIN topics t ON t.id = v.topic_id "
            "WHERE v.id = %s",
            (video_id,),
        )
        row = cur.fetchone()
    if row and row[0] and isinstance(row[0], dict):
        g = row[0].get("game")
        if isinstance(g, str) and g:
            return g
    return None


def _pick_footage(conn, *, game: str | None, tag: str) -> Footage | None:
    """Подобрать клип: тег ОБЯЗАТЕЛЬНО матчится; если тег universal или game
    не задан — фильтр по игре отключается. Меньшее used_count выигрывает.
    """
    universal = tag in _UNIVERSAL_TAGS
    game_irrelevant = (game is None) or universal
    sql = (
        "SELECT id::text, r2_key, game, tags, duration_sec "
        "FROM footage "
        "WHERE %s = ANY(tags) "
        "  AND (%s OR game = %s) "
        "ORDER BY used_count ASC, created_at "
        "LIMIT 1"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (tag, game_irrelevant, game or ""))
        row = cur.fetchone()
    if not row:
        return None
    return Footage(
        id=row[0], r2_key=row[1], game=row[2],
        tags=list(row[3] or []), duration_sec=float(row[4]),
    )


def _increment_used(conn, footage_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE footage SET used_count = used_count + 1 WHERE id = %s",
            (footage_id,),
        )


def _save_render_key(conn, video_id: str, key: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE videos SET render_r2_key = %s WHERE id = %s",
            (key, video_id),
        )


def _list_music_keys(conn=None) -> list[str]:
    """Список mp3 в R2 префикс 'music/'. Без БД."""
    s = require("epic1")
    cli = r2.client()
    try:
        resp = cli.list_objects_v2(Bucket=s.R2_BUCKET_FOOTAGE, Prefix="music/")
    except Exception:
        return []
    keys = [
        obj["Key"] for obj in resp.get("Contents", [])
        if obj["Key"].lower().endswith((".mp3", ".m4a", ".wav"))
    ]
    return keys


# --------- сборка ---------

def _template_name(fmt: str) -> str:
    return {
        "short": "short_v1",
        "long_gaming": "long_v1",
        "long_setup": "setup_v1",
        "bridge": "long_v1",
    }.get(fmt, "long_v1")


# --------- short format: внешний Creatomate-template ---------

# Маппинг от семантических имён переменных шаблона short к слотам.
SHORT_VIDEO_SLOTS = ("Video-1", "Video-2", "Video-3")
SHORT_FACT_SLOTS = ("Fact-1", "Fact-2", "Fact-3", "Fact-4", "Fact-5")
SHORT_INTRO_SLOT = "Intro-Text"
SHORT_OUTRO_SLOT = "Outro-Text"
SHORT_MUSIC_SLOT = "Music"


def build_short_modifications(
    *,
    script: dict[str, Any],
    video_urls: list[str],
    music_url: str | None,
) -> dict[str, Any]:
    """Маппинг переменных pre-built Creatomate-шаблона для short формата.

    Video-1/2/3 ← video_urls[0..2] (3 клипа, при нехватке циклим)
    Intro-Text   ← script.hook
    Fact-1..5    ← первые 5 sections.voice
    Outro-Text   ← script.cta
    Music        ← music_url (signed R2 URL ElevenLabs music)
    """
    if not video_urls:
        raise ValueError("build_short_modifications: video_urls is empty")
    mods: dict[str, Any] = {
        SHORT_INTRO_SLOT: (script.get("hook") or "")[:200],
        SHORT_OUTRO_SLOT: (script.get("cta") or "")[:200],
    }
    for i, slot in enumerate(SHORT_VIDEO_SLOTS):
        mods[slot] = video_urls[i % len(video_urls)]
    sections = script.get("sections") or []
    for i, slot in enumerate(SHORT_FACT_SLOTS):
        voice = ""
        if i < len(sections):
            voice = (sections[i].get("voice") or "")[:200]
        mods[slot] = voice
    if music_url:
        mods[SHORT_MUSIC_SLOT] = music_url
    return mods


def _music_for_short(video_id: str, topic_title: str,
                     duration_sec: float) -> str | None:
    """Сгенерировать музыку через ElevenLabs Music, залить в R2,
    вернуть signed URL. None если не получилось — Creatomate отрендерит
    шаблон с дефолтной музыкой из template (если есть)."""
    from src.core import elevenlabs as el

    length_ms = max(15_000, int(duration_sec * 1000) + 2000)
    try:
        audio = el.music_for_topic(topic_title, music_length_ms=length_ms)
    except Exception:
        log.warning("music_for_topic failed video=%s", video_id, exc_info=True)
        return None
    s = require("epic1")
    key = f"music/short/{video_id}.mp3"
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp.write(audio)
        tmp_path = tmp.name
    try:
        r2.upload_file(s.R2_BUCKET_RENDERS, key, tmp_path,
                       content_type="audio/mpeg")
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return _signed(s.R2_BUCKET_RENDERS, key)


def _signed(bucket: str, key: str, ttl: int = 3600) -> str:
    return r2.signed_url(bucket, key, ttl_seconds=ttl)


def _build_modifications(
    *,
    fmt: str,
    template: dict[str, Any],
    footage_picks: list[Footage],
    voice_url: str,
    voice_duration: float,
    section_durations: list[float],
    subtitles_text: str,
    music_url: str | None,
    generated_clip_urls: list[str] | None = None,
) -> dict[str, Any]:
    """Если задан `generated_clip_urls` — берём AI-клипы по индексу
    секции (E8.5). Иначе работаем по старой схеме `footage_picks` из
    собственного банка."""
    settings = require("epic1")
    footage_bucket = settings.R2_BUCKET_FOOTAGE
    mods: dict[str, Any] = {
        "voice.source": voice_url,
        "voice.duration": voice_duration,
        "subtitles.text": subtitles_text,
        "subtitles.duration": voice_duration,
    }
    if music_url:
        mods["music.source"] = music_url
        mods["music.duration"] = voice_duration

    elem_ids = [e.get("id") for e in template.get("elements", [])]
    footage_ids = [eid for eid in elem_ids if eid and eid.startswith("footage")]

    t_cursor = 0.0
    for i, slot_id in enumerate(footage_ids):
        # длительность сегмента
        if len(footage_ids) == 1:
            seg_dur = voice_duration
        else:
            seg_dur = voice_duration / len(footage_ids)

        if generated_clip_urls:
            # AI-форматы: берём клип по индексу секции (циклически если слотов больше).
            src = generated_clip_urls[i % len(generated_clip_urls)]
        else:
            pick = footage_picks[i % len(footage_picks)]
            src = _signed(footage_bucket, pick.r2_key)

        mods[f"{slot_id}.source"] = src
        mods[f"{slot_id}.time"] = t_cursor
        mods[f"{slot_id}.duration"] = seg_dur
        t_cursor += seg_dur

    return mods


def _subtitles_concat(script: dict[str, Any]) -> str:
    parts: list[str] = []
    if script.get("hook"):
        parts.append(script["hook"])
    for s in script.get("sections") or []:
        if s.get("voice"):
            parts.append(s["voice"])
    if script.get("cta"):
        parts.append(script["cta"])
    return " ".join(parts)


# --------- основной шаг ---------

class FootageGap(RuntimeError):
    def __init__(self, missing: list[str]) -> None:
        super().__init__(f"footage_gap: {', '.join(missing)}")
        self.missing = missing


def _resolve_footage(conn, *, game: str | None,
                     sections: list[dict[str, Any]]) -> tuple[list[Footage], list[str]]:
    """Подобрать клипы для всех footage_tag из секций.
    Возвращает (picks, missing_descriptors)."""
    picks: list[Footage] = []
    missing: list[str] = []
    seen: dict[str, Footage] = {}
    for s in sections:
        tag = (s.get("footage_tag") or "").strip()
        if not tag:
            tag = "b-roll"
        cache_key = f"{game or '*'}/{tag}"
        if cache_key in seen:
            picks.append(seen[cache_key])
            continue
        f = _pick_footage(conn, game=game, tag=tag)
        if f is None:
            # пробуем универсальный fallback
            f = _pick_footage(conn, game=None, tag="b-roll") or \
                _pick_footage(conn, game=None, tag="scenic")
        if f is None:
            missing.append(f"{game or '*'}/{tag}")
            continue
        picks.append(f)
        seen[cache_key] = f
    return picks, missing


def process_video(claimed: Claimed) -> str:
    video_id = claimed.video_id
    log.info("assembler start video=%s", video_id)
    require("epic2")

    with connection() as conn:
        v = _load_video(conn, video_id)
        if not v:
            raise RuntimeError(f"video {video_id} not found")
        script = v["script"] or {}
        fmt = v["format"]
        voice_key = v["voice_r2_key"]
        topic_title = v["topic_title"]
        game = _topic_game(conn, video_id)

    sections = script.get("sections") or []
    if not sections or not voice_key:
        StateMachine(WORKER_NAME).transition(
            video_id, "voiced", "failed",
            last_error="missing sections or voice_r2_key",
        )
        return "failed"

    s_epic1 = require("epic1")
    picks: list[Footage] = []
    generated_clip_urls: list[str] | None = None

    if uses_generated_clips(fmt):
        # E8.5: short/long_setup — берём AI-клипы из generated/<video_id>/<idx>.mp4
        missing_clips: list[int] = []
        urls: list[str] = []
        for i, sec in enumerate(sections):
            key = sec.get("generated_clip_r2_key")
            if not key:
                missing_clips.append(i)
                continue
            urls.append(_signed(s_epic1.R2_BUCKET_RENDERS, key))
        if missing_clips:
            err = f"generated_clip_gap: sections {missing_clips}"
            StateMachine(WORKER_NAME).transition(
                video_id, "voiced", "failed", last_error=err,
            )
            send_alert(f"📭 {WORKER_NAME} {err} (video={video_id}, '{topic_title}')")
            return "failed"
        generated_clip_urls = urls
    else:
        # long_gaming/bridge: собственный банк, старая логика
        with connection() as conn:
            picks, missing = _resolve_footage(conn, game=game, sections=sections)
            if missing:
                err = f"footage_gap: {', '.join(missing)}"
                StateMachine(WORKER_NAME).transition(
                    video_id, "voiced", "failed", last_error=err,
                )
                send_alert(f"📭 {WORKER_NAME} {err} (video={video_id}, '{topic_title}')")
                return "failed"
            used_increments = defaultdict(int)
            for f in picks:
                used_increments[f.id] += 1
            with conn.transaction():
                for fid, n in used_increments.items():
                    for _ in range(n):
                        _increment_used(conn, fid)

    # voice URL + длительность
    voice_url = _signed(s_epic1.R2_BUCKET_RENDERS, voice_key)
    voice_duration = float(
        script.get("total_voice_duration_sec") or sum(
            (s.get("voice_duration_sec") or 0) for s in sections
        ) or 30.0
    )
    section_durations = [float(s.get("voice_duration_sec") or 0) for s in sections]

    # Short с готовым Creatomate-шаблоном (template_id из ENV) — branch
    # после AL-003: вместо инлайн-JSON отправляем template_id + новый маппинг
    # Video-1/2/3 + Intro/Fact/Outro + ElevenLabs Music.
    if fmt == "short" and s_epic1 and getattr(
            require("epic2"), "CREATOMATE_TEMPLATE_SHORT", "") and generated_clip_urls:
        template_id = require("epic2").CREATOMATE_TEMPLATE_SHORT
        topic_title = (topic_title or "").strip()
        music_url = _music_for_short(video_id, topic_title, voice_duration)
        mods = build_short_modifications(
            script=script,
            video_urls=generated_clip_urls,
            music_url=music_url,
        )
        log.info(
            "assembler short video=%s via template_id=%s clips=%d music=%s",
            video_id, template_id, len(generated_clip_urls),
            "ok" if music_url else "default",
        )
        result = creatomate.render_and_wait(
            template_id=template_id, modifications=mods, deadline_sec=900,
        )
    else:
        template = creatomate.load_template(_template_name(fmt))
        mods = _build_modifications(
            fmt=fmt,
            template=template,
            footage_picks=picks,
            voice_url=voice_url,
            voice_duration=voice_duration,
            section_durations=section_durations,
            subtitles_text=_subtitles_concat(script),
            music_url=None,
            generated_clip_urls=generated_clip_urls,
        )
        src_count = (len(generated_clip_urls) if generated_clip_urls
                     else len(picks))
        log.info("assembler render video=%s template=%s sources=%d (%s)",
                 video_id, _template_name(fmt), src_count,
                 "ai" if generated_clip_urls else "bank")
        result = creatomate.render_and_wait(
            source=template, modifications=mods, deadline_sec=900,
        )
    rendered_url = result.get("url")
    if not rendered_url:
        raise RuntimeError(f"creatomate returned no url: {result}")

    # скачать и положить в R2
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        with httpx.stream("GET", rendered_url, timeout=120.0) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_bytes():
                tmp.write(chunk)
        tmp_path = tmp.name

    render_key = f"renders/{video_id}.mp4"
    r2.upload_file(s_epic1.R2_BUCKET_RENDERS, render_key, tmp_path,
                   content_type="video/mp4")

    with connection() as conn:
        with conn.transaction():
            _save_render_key(conn, video_id, render_key)

    StateMachine(WORKER_NAME).transition(
        video_id, "voiced", "assembled",
        detail=f"footage={len(picks)} duration={voice_duration:.1f}s",
    )
    log.info("assembler done video=%s key=%s", video_id, render_key)
    return "assembled"


def run_once(*, limit: int = 1) -> int:
    sm = StateMachine(WORKER_NAME)
    claimed = sm.claim("voiced", limit=limit)
    processed = 0
    for c in claimed:
        try:
            process_video(c)
            processed += 1
        except Exception as exc:
            log.exception("assembler failed video=%s", c.video_id)
            # Без transition в failed запись остаётся voiced и снова
            # захватывается каждые 15s → hot-loop. Помечаем failed.
            try:
                sm.transition(
                    c.video_id, "voiced", "failed",
                    last_error=f"{type(exc).__name__}: {str(exc)[:240]}",
                )
            except Exception:
                log.warning("assembler: voiced→failed transition failed",
                            exc_info=True)
    return processed


__all__ = [
    "WORKER_NAME",
    "process_video",
    "run_once",
    "FootageGap",
]
