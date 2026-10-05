"""Footage Generator (E8.4).

Только для format ∈ {short, long_setup} (AI-форматы по AL-001).

Цикл:
  1. claim('scripted') WHERE format IN ('short', 'long_setup')
  2. для каждой секции с scene_description:
       fal.ai/kling-video → mp4 URL
       скачать → R2 generated/<video_id>/<idx>.mp4
       record key в script.sections[i].generated_clip_r2_key
  3. 3 retry на клип. Failed → fallback:
       fal.ai/flux text-to-image по той же scene_description → JPG;
       Creatomate still_v1 (Ken Burns 5с) с этим JPG → mp4;
       упакован обратно как обычный generated_clip.
  4. transition scripted → footage_ready.

Long_gaming/bridge через этого воркера не идут — scriptwriter сразу
переводит их в footage_ready (E8.2).
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

import httpx

from src.bot.alerts import send_alert
from src.core import creatomate, fal, kling, r2
from src.core.config import require
from src.core.db import connection
from src.core.state_machine import StateMachine

log = logging.getLogger(__name__)

WORKER_NAME = "footage_generator"
MAX_KLING_RETRY = 3
AI_FORMATS = ("short", "long_setup")


# --------- репозиторий ---------

def _claim_scripted_ai() -> dict | None:
    sql = (
        "SELECT id::text, format::text, script "
        "FROM videos "
        "WHERE status = 'scripted' "
        "  AND format IN ('short', 'long_setup') "
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
    return {"id": row[0], "format": row[1], "script": row[2] or {}}


def _save_script(conn, video_id: str, script: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE videos SET script = %s::jsonb WHERE id = %s",
            (json.dumps(script), video_id),
        )


# --------- генерация клипа ---------

def _download_to_r2(remote_url: str, *, bucket: str, key: str,
                    content_type: str = "video/mp4") -> int:
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        with httpx.stream("GET", remote_url, timeout=180.0) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_bytes():
                tmp.write(chunk)
        path = Path(tmp.name)
    try:
        size = path.stat().st_size
        r2.upload_file(bucket, key, str(path), content_type=content_type)
    finally:
        path.unlink(missing_ok=True)
    return size


def _gen_kling(prompt: str, *, duration: int = 5) -> str:
    """Сгенерировать клип через Kling и вернуть URL итогового mp4."""
    return kling.text_to_video(prompt, duration=duration,
                               aspect_ratio="16:9", deadline_sec=900)


def _gen_fallback_kenburns(prompt: str, *, video_id: str, idx: int) -> str:
    """Fallback: Flux картинка → Creatomate still_v1 (Ken Burns 5с)."""
    log.info("fallback Ken Burns for video=%s section=%d", video_id, idx)
    flux_urls = fal.text_to_image(
        prompt + ", cinematic 1080p, dramatic lighting, ultra-detailed",
        width=1920, height=1080, num_images=1,
    )
    bg_url = flux_urls[0]
    template = creatomate.load_template("still_v1")
    mods = {"still.source": bg_url}
    result = creatomate.render_and_wait(
        source=template, modifications=mods, deadline_sec=180,
    )
    url = result.get("url")
    if not url:
        raise RuntimeError(f"still_v1 render no url: {result}")
    return url


def generate_section(prompt: str, *,
                     video_id: str, idx: int,
                     bucket: str, duration: int = 5,
                     max_retry: int = MAX_KLING_RETRY) -> tuple[str, str]:
    """Возвращает (r2_key, source — 'kling' | 'fallback').
    Пробует Kling до `max_retry` раз; затем — Creatomate Ken Burns still."""
    key = f"generated/{video_id}/{idx:02d}.mp4"
    last: Exception | None = None
    for attempt in range(1, max_retry + 1):
        try:
            url = _gen_kling(prompt, duration=duration)
            size = _download_to_r2(url, bucket=bucket, key=key)
            log.info("kling ok video=%s idx=%d size=%d attempt=%d",
                     video_id, idx, size, attempt)
            return key, "kling"
        except Exception as exc:
            last = exc
            log.warning("kling attempt %d/%d failed video=%s idx=%d: %s",
                        attempt, max_retry, video_id, idx, exc)

    # Fallback Ken Burns
    try:
        url = _gen_fallback_kenburns(prompt, video_id=video_id, idx=idx)
        size = _download_to_r2(url, bucket=bucket, key=key)
        log.info("fallback ok video=%s idx=%d size=%d", video_id, idx, size)
        send_alert(
            f"⚠️ Kling fell back to Ken Burns still for video={video_id} "
            f"section={idx} (after {max_retry} Kling retries). "
            f"Last error: {last}"
        )
        return key, "fallback"
    except Exception as exc:
        raise RuntimeError(
            f"both Kling ({last}) and fallback ({exc}) failed for "
            f"video={video_id} idx={idx}"
        ) from exc


# --------- основной шаг ---------

def process_video(record: dict) -> str:
    require("epic2")
    s = require("epic1")
    video_id = record["id"]
    fmt = record["format"]
    script = record["script"] or {}
    sections = script.get("sections") or []

    if not sections:
        StateMachine(WORKER_NAME).transition(
            video_id, "scripted", "failed",
            last_error="no sections to generate from",
        )
        return "failed"

    log.info("footage_generator start video=%s sections=%d", video_id, len(sections))

    bucket = s.R2_BUCKET_RENDERS
    any_fallback = False
    for idx, sec in enumerate(sections):
        if sec.get("generated_clip_r2_key"):
            log.info("skip already-generated video=%s idx=%d", video_id, idx)
            continue
        prompt = (sec.get("scene_description") or "").strip()
        if not prompt:
            raise RuntimeError(f"section[{idx}] missing scene_description")
        key, source = generate_section(
            prompt, video_id=video_id, idx=idx, bucket=bucket,
            duration=5,
        )
        sec["generated_clip_r2_key"] = key
        sec["generated_clip_source"] = source
        if source == "fallback":
            any_fallback = True

    # persist обновлённый script
    with connection() as conn:
        with conn.transaction():
            _save_script(conn, video_id, script)

    detail = f"sections={len(sections)}"
    if any_fallback:
        detail += " with_fallback"
    StateMachine(WORKER_NAME).transition(
        video_id, "scripted", "footage_ready", detail=detail,
    )
    return "footage_ready"


def run_once() -> int:
    rec = _claim_scripted_ai()
    if not rec:
        return 0
    try:
        process_video(rec)
        return 1
    except Exception as exc:
        log.exception("footage_generator failed video=%s", rec["id"])
        # Без transition в failed запись остаётся scripted и снова
        # захватывается каждые 15s → hot-loop.
        try:
            StateMachine(WORKER_NAME).transition(
                rec["id"], "scripted", "failed",
                last_error=f"{type(exc).__name__}: {str(exc)[:240]}",
            )
        except Exception:
            log.warning("footage_generator: scripted→failed transition failed",
                        exc_info=True)
        return 0


__all__ = [
    "WORKER_NAME",
    "MAX_KLING_RETRY",
    "generate_section",
    "process_video",
    "run_once",
]
