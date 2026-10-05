"""Thumbnail worker (спецификация 6.6 ТЗ).

Только длинные форматы (long_gaming, long_setup). Шорты пропускают этот
шаг — SEO Packager их доберёт в assembled→packaged напрямую.

Цикл (claim — кастомный SELECT, чтобы не конкурировать с seo_packager):
  1. найти video в assembled, формат long_*, без thumbnail_r2_key
  2. сгенерировать text-оверлей (LLM, ≤4 слова, два варианта A / B)
  3. сгенерировать фон через fal.ai Flux (по два промпт-варианта)
  4. для каждого варианта — Creatomate render с шаблоном thumb_v1
  5. скачать JPG → upload в R2_BUCKET_RENDERS под thumbnails/<uuid>(_b).jpg
  6. update thumbnail_r2_key (= A); вариант B рядом с суффиксом _b

Без перехода статуса — seo_packager заберёт запись на следующем тике.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import httpx

from src.core import creatomate, fal, llm, r2
from src.core.config import project_root, require
from src.core.db import connection

log = logging.getLogger(__name__)

WORKER_NAME = "thumbnail"

_FAL_PROMPT_A = (
    "Cinematic gaming thumbnail background, no text, no UI, no logos. "
    "Theme: {topic}. Photoreal, dramatic moody lighting, deep contrast, "
    "shallow depth of field. Centered composition with empty lower-third "
    "space for text overlay."
)
_FAL_PROMPT_B = (
    "High-energy gaming thumbnail background, no text, no UI, no logos. "
    "Theme: {topic}. Bright key light, saturated colors, kinetic motion blur, "
    "wide-angle composition. Clean lower area for text overlay."
)


# --------- репозиторий ---------

def _claim_long_assembled() -> tuple[str, str, str] | None:
    """SELECT ... FOR UPDATE SKIP LOCKED — берём long-видео в assembled
    без thumbnail_r2_key. Возвращаем (video_id, format, title)."""
    sql = (
        "SELECT id::text, format::text, title "
        "FROM videos "
        "WHERE status = 'assembled' "
        "  AND format IN ('long_gaming', 'long_setup') "
        "  AND thumbnail_r2_key IS NULL "
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
    return row[0], row[1], row[2] or ""


def _save_thumb_key(conn, video_id: str, key: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE videos SET thumbnail_r2_key = %s WHERE id = %s",
            (key, video_id),
        )


# --------- генерация ---------

def _overlay_phrases(topic_title: str) -> tuple[str, str]:
    template = (project_root() / "templates" / "prompts" / "thumbnail_text.md").read_text()
    prompt = template.format(topic_title=topic_title)
    obj = llm.complete_json(
        [{"role": "user", "content": prompt}],
        system="You write short overlay copy for YouTube thumbnails.",
        max_tokens=200,
        worker=WORKER_NAME,
    )
    a = (obj.get("variant_a") or "Watch This")[:60]
    b = (obj.get("variant_b") or "You're Wrong")[:60]
    # ≤4 слов hard cap
    a = " ".join(a.split()[:4]).strip()
    b = " ".join(b.split()[:4]).strip()
    return a, b


def _flux_bg(prompt: str) -> str:
    urls = fal.text_to_image(prompt, width=1280, height=720, num_images=1)
    return urls[0]


def _render_variant(*, bg_url: str, overlay: str) -> str:
    template = creatomate.load_template("thumb_v1")
    mods = {
        "background.source": bg_url,
        "title.text": overlay,
    }
    result = creatomate.render_and_wait(
        source=template, modifications=mods, deadline_sec=180,
    )
    url = result.get("url")
    if not url:
        raise RuntimeError(f"thumb render no url: {result}")
    return url


def _store_jpg(remote_url: str, *, key: str) -> None:
    s = require("epic1")
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        with httpx.stream("GET", remote_url, timeout=60.0) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_bytes():
                tmp.write(chunk)
        path = Path(tmp.name)
    try:
        r2.upload_file(s.R2_BUCKET_RENDERS, key, str(path), content_type="image/jpeg")
    finally:
        path.unlink(missing_ok=True)


def process_video(video_id: str, fmt: str, title: str) -> str:
    log.info("thumbnail start video=%s format=%s", video_id, fmt)
    require("epic2")
    if fmt not in ("long_gaming", "long_setup"):
        return "skipped"

    phrase_a, phrase_b = _overlay_phrases(title or "Untitled")
    log.info("overlay phrases A=%r B=%r", phrase_a, phrase_b)

    bg_a = _flux_bg(_FAL_PROMPT_A.format(topic=title))
    bg_b = _flux_bg(_FAL_PROMPT_B.format(topic=title))

    url_a = _render_variant(bg_url=bg_a, overlay=phrase_a)
    url_b = _render_variant(bg_url=bg_b, overlay=phrase_b)

    key_a = f"thumbnails/{video_id}.jpg"
    key_b = f"thumbnails/{video_id}_b.jpg"
    _store_jpg(url_a, key=key_a)
    _store_jpg(url_b, key=key_b)

    with connection() as conn:
        with conn.transaction():
            _save_thumb_key(conn, video_id, key_a)

    log.info("thumbnail done video=%s a=%s b=%s", video_id, key_a, key_b)
    return "done"


def run_once() -> int:
    pick = _claim_long_assembled()
    if not pick:
        return 0
    video_id, fmt, title = pick
    try:
        process_video(video_id, fmt, title)
        return 1
    except Exception:
        log.exception("thumbnail failed video=%s", video_id)
        return 0


__all__ = ["WORKER_NAME", "process_video", "run_once"]
