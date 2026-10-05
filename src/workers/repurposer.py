"""Repurposer (спецификация 6.11 ТЗ).

Поллинг videos.status='published' без строк в distributions →
fan-out строк (blog/pinterest/threads) + батч-генерация контента в ОДНОМ
вызове Claude. Pin-картинка через Creatomate pin_v1 (1000×1500).
Выход: distributions.status='content_ready'.

Изоляция (E6.7): сбой repurposer не трогает videos.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

import httpx

from src.core import creatomate, llm, r2, settings_store
from src.core.config import project_root, require
from src.core.db import connection

log = logging.getLogger(__name__)

WORKER_NAME = "repurposer"


# --------- queries ---------

def _claim_published() -> dict | None:
    """Найти published-видео без строк в distributions. FOR UPDATE SKIP LOCKED."""
    sql = (
        "SELECT v.id::text, v.format::text, v.title, COALESCE(v.script, '{}'::jsonb), "
        "       COALESCE(v.seo, '{}'::jsonb), v.thumbnail_r2_key, v.render_r2_key "
        "FROM videos v "
        "WHERE v.status = 'published' "
        "  AND NOT EXISTS (SELECT 1 FROM distributions d WHERE d.video_id = v.id) "
        "ORDER BY v.status_updated_at "
        "FOR UPDATE OF v SKIP LOCKED LIMIT 1"
    )
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "format": row[1], "title": row[2] or "",
        "script": row[3] or {}, "seo": row[4] or {},
        "thumbnail_r2_key": row[5],
        "render_r2_key": row[6],
    }


def _channels_for(fmt: str) -> dict[str, str]:
    """Возвращает {channel: initial_status} в соответствии с разделом 6.11.

    * blog только для длинных
    * pinterest для длинных + setup
    * threads: если threads_enabled — pending, иначе skipped
    """
    out: dict[str, str] = {}
    # blog — только длинные форматы
    if fmt.startswith("long"):
        out["blog"] = "pending"
    # pinterest — все опубликованные видео (E8.7 расширение AL-001)
    out["pinterest"] = "pending"
    try:
        threads_on = settings_store.is_threads_enabled()
    except Exception:
        threads_on = settings_store.get_flag("threads_enabled", False)
    out["threads"] = "pending" if threads_on else "skipped"
    return out


def _fanout(conn, video_id: str, mapping: dict[str, str]) -> None:
    with conn.cursor() as cur:
        for ch, status in mapping.items():
            cur.execute(
                "INSERT INTO distributions (video_id, channel, status) "
                "VALUES (%s, %s::dist_channel, %s::dist_status) "
                "ON CONFLICT (video_id, channel) DO NOTHING",
                (video_id, ch, status),
            )


def _save_content(conn, video_id: str, channel: str, content: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE distributions "
            "SET content = %s::jsonb, status = 'content_ready' "
            "WHERE video_id = %s AND channel = %s::dist_channel "
            "  AND status = 'pending'",
            (json.dumps(content), video_id, channel),
        )


# --------- pin image ---------

def _extract_first_frame(video_r2_key: str, video_id: str) -> str:
    """Скачать render mp4 → ffmpeg первый кадр → залить как pin-background
    в R2 под pins/<video_id>_frame.jpg. Возвращает r2_key."""
    import subprocess
    s = require("epic1")
    with tempfile.TemporaryDirectory(prefix=f"pin-{video_id}-") as td:
        td_path = Path(td)
        mp4_path = td_path / "render.mp4"
        r2.client().download_file(s.R2_BUCKET_RENDERS, video_r2_key, str(mp4_path))
        frame_path = td_path / "frame.jpg"
        subprocess.check_call([
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(mp4_path), "-vf", "select=eq(n\\,30)",  # 30-й кадр (~1с)
            "-frames:v", "1", "-q:v", "3",
            str(frame_path),
        ])
        key = f"pins/{video_id}_frame.jpg"
        r2.upload_file(s.R2_BUCKET_RENDERS, key, str(frame_path),
                       content_type="image/jpeg")
    return key


def _build_pin(*, title: str, overlay: str,
               bg_url: str, video_id: str) -> str:
    template = creatomate.load_template("pin_v1")
    mods = {
        "background.source": bg_url,
        "title.text": overlay,
        "subtitle.text": title[:60],
    }
    result = creatomate.render_and_wait(
        source=template, modifications=mods, deadline_sec=180,
    )
    url = result.get("url")
    if not url:
        raise RuntimeError(f"pin render no url: {result}")

    s = require("epic1")
    key = f"pins/{video_id}.jpg"
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        with httpx.stream("GET", url, timeout=60.0) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_bytes():
                tmp.write(chunk)
        path = Path(tmp.name)
    try:
        r2.upload_file(s.R2_BUCKET_RENDERS, key, str(path),
                       content_type="image/jpeg")
    finally:
        path.unlink(missing_ok=True)
    return key


# --------- LLM ---------

def _build_prompt(*, video: dict, channels: list[str]) -> str:
    template = (project_root() / "templates" / "prompts" / "repurposer.md").read_text()
    return template.format(
        title=video["title"],
        channels=", ".join(channels),
        description_body=(video["seo"].get("description_body")
                          or video["seo"].get("description") or "")[:4000],
        script_json=json.dumps(video["script"], ensure_ascii=False),
        products_json=json.dumps(video["seo"].get("products") or [],
                                 ensure_ascii=False),
    )


# --------- основной шаг ---------

def process_video(video: dict) -> str:
    require("epic2")
    video_id = video["id"]
    fmt = video["format"]
    log.info("repurposer start video=%s format=%s", video_id, fmt)

    channels_map = _channels_for(fmt)
    with connection() as conn:
        with conn.transaction():
            _fanout(conn, video_id, channels_map)

    active = [c for c, st in channels_map.items() if st == "pending"]
    if not active:
        return "skipped"

    # один LLM-вызов на видео
    prompt = _build_prompt(video=video, channels=active)
    obj = llm.complete_json(
        [{"role": "user", "content": prompt}],
        system="You are the cross-channel copywriter. No marketing fluff.",
        max_tokens=6000,
        worker=WORKER_NAME,
        video_id=video_id,
    )

    # Pin-картинка только если pinterest в active
    if "pinterest" in active and isinstance(obj.get("pinterest"), dict):
        s = require("epic1")
        bg_key = video["thumbnail_r2_key"]
        # E8.7: для короткого формата (нет отдельного thumbnail) берём
        # первый «контентный» кадр render mp4 через ffmpeg.
        if not bg_key and video.get("render_r2_key"):
            try:
                bg_key = _extract_first_frame(video["render_r2_key"], video_id)
                log.info("repurposer: extracted frame for short video=%s key=%s",
                         video_id, bg_key)
            except Exception:
                log.warning("repurposer: frame extract failed video=%s",
                            video_id, exc_info=True)
        if not bg_key:
            log.warning("repurposer: no background for pin video=%s", video_id)
        else:
            bg_url = r2.signed_url(s.R2_BUCKET_RENDERS, bg_key)
            overlay = obj["pinterest"].get("overlay") or video["title"][:30]
            pin_key = _build_pin(
                title=video["title"], overlay=overlay,
                bg_url=bg_url, video_id=video_id,
            )
            obj["pinterest"]["image_r2_key"] = pin_key

    # сохранить content per канал
    with connection() as conn:
        with conn.transaction():
            for ch in active:
                content = obj.get(ch)
                if not isinstance(content, dict):
                    log.warning("repurposer: missing %s in LLM output", ch)
                    continue
                _save_content(conn, video_id, ch, content)

    return "ok"


def run_once() -> int:
    pick = _claim_published()
    if not pick:
        return 0
    try:
        process_video(pick)
        return 1
    except Exception:
        log.exception("repurposer failed video=%s", pick["id"])
        return 0


__all__ = ["WORKER_NAME", "process_video", "run_once"]
