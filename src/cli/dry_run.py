"""E2E dry-run эпика 2 (задача 2.9 ТЗ).

Создаёт approved-тему, проводит её через всю цепочку воркеров без
вмешательства человека, на каждом шаге печатает статус и время.

Использование:
    python -m src.cli.dry_run --format short
    python -m src.cli.dry_run --format long_gaming
    python -m src.cli.dry_run --format long_gaming --keep   # не чистить за собой

Требует БД и R2; запускается внутри прод-контейнера на VPS:
    docker compose exec -T app python -m src.cli.dry_run --format short
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid

from src.core.db import connection
from src.core.state_machine import Claimed
from src.workers import assembler, scriptwriter, seo_packager, thumbnail, voice


# Безопасные «нейтральные» темы, успешно проходящие факт-чек
_TOPICS = {
    "short": {
        "title":         "60-second guide: CS2 awping fundamentals",
        "reasoning":     "evergreen tutorial; cs2 footage in bank",
        "game":          "cs2",
        "format":        "short",
    },
    "long_gaming": {
        "title":         "Five common CS2 positioning habits that lose rounds",
        "reasoning":     "high search volume; cs2 footage in bank",
        "game":          "cs2",
        "format":        "long_gaming",
    },
    "long_setup": {
        "title":         "How to fit a 240Hz monitor into a small desk",
        "reasoning":     "battlestations sub trending; uses peripheral products",
        "game":          None,
        "format":        "long_setup",
    },
}


def _seed(fmt: str) -> tuple[str, str]:
    spec = _TOPICS[fmt]
    topic_id = str(uuid.uuid4())
    video_id = str(uuid.uuid4())
    payload = json.dumps({"game": spec["game"]})
    dedup = f"dryrun-{uuid.uuid4()}"
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO topics "
                    "(id, source, title, raw_payload, score, score_reasoning, "
                    " suggested_format, dedup_hash) "
                    "VALUES (%s, 'dryrun', %s, %s::jsonb, 88, %s, %s::video_format, %s)",
                    (topic_id, spec["title"], payload, spec["reasoning"], fmt, dedup),
                )
                cur.execute(
                    "INSERT INTO videos (id, topic_id, format, status) "
                    "VALUES (%s, %s, %s::video_format, 'approved')",
                    (video_id, topic_id, fmt),
                )
    return topic_id, video_id


def _cleanup(topic_id: str, video_id: str) -> None:
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("DELETE FROM status_log WHERE video_id = %s", (video_id,))
                cur.execute("DELETE FROM videos WHERE id = %s", (video_id,))
                cur.execute("DELETE FROM topics WHERE id = %s", (topic_id,))


def _fetch(video_id: str) -> dict:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status::text, format::text, title, voice_r2_key, "
                "       render_r2_key, thumbnail_r2_key, last_error "
                "FROM videos WHERE id = %s",
                (video_id,),
            )
            row = cur.fetchone()
    if not row:
        return {}
    return {
        "status": row[0], "format": row[1], "title": row[2],
        "voice_r2_key": row[3], "render_r2_key": row[4],
        "thumbnail_r2_key": row[5], "last_error": row[6],
    }


def _print_step(label: str, t0: float, info: dict) -> None:
    dur = time.monotonic() - t0
    print(f"  [{dur:5.1f}s] {label:14s} status={info.get('status'):>16s} "
          f"err={info.get('last_error') or '-'}")


def run(fmt: str, *, keep: bool = False) -> int:
    print(f"\n=== dry-run {fmt} ===")
    topic_id, video_id = _seed(fmt)
    print(f"video_id: {video_id}")
    t0 = time.monotonic()
    fail: str | None = None

    try:
        # 1. scriptwriter
        scriptwriter.process_video(
            Claimed(video_id=video_id, status="approved", attempts=0,
                    format=fmt, payload={}),
        )
        info = _fetch(video_id)
        _print_step("scriptwriter", t0, info)
        if info["status"] != "scripted":
            fail = f"scriptwriter→{info['status']}"
            return 1

        # 2. voice
        voice.process_video(
            Claimed(video_id=video_id, status="scripted", attempts=0,
                    format=fmt, payload={}),
        )
        info = _fetch(video_id)
        _print_step("voice", t0, info)
        if info["status"] != "voiced":
            fail = f"voice→{info['status']}"
            return 1

        # 3. assembler
        assembler.process_video(
            Claimed(video_id=video_id, status="voiced", attempts=0,
                    format=fmt, payload={}),
        )
        info = _fetch(video_id)
        _print_step("assembler", t0, info)
        if info["status"] != "assembled":
            fail = f"assembler→{info['status']}"
            return 1

        # 4. thumbnail — только длинные
        if fmt.startswith("long"):
            thumbnail.process_video(video_id, fmt, info["title"])
            info = _fetch(video_id)
            _print_step("thumbnail", t0, info)
            if not info["thumbnail_r2_key"]:
                fail = "thumbnail_r2_key missing"
                return 1

        # 5. seo_packager
        # script нам теперь нужен для process_one
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT script, voice_r2_key, title FROM videos WHERE id = %s",
                    (video_id,),
                )
                script, voice_key, title = cur.fetchone()
        seo_packager.process_one(video_id, fmt, title, script or {}, voice_key)
        info = _fetch(video_id)
        _print_step("seo_packager", t0, info)
        if info["status"] != "ready_for_review":
            fail = f"seo→{info['status']}"
            return 1

        total = time.monotonic() - t0
        print(f"✓ {fmt} reached ready_for_review in {total:.1f}s")
        print(f"  title:   {info['title']}")
        print(f"  render:  {info['render_r2_key']}")
        if info["thumbnail_r2_key"]:
            print(f"  thumb:   {info['thumbnail_r2_key']}")
        return 0
    finally:
        if not keep:
            _cleanup(topic_id, video_id)
            print(f"  cleanup: removed topic+video")
        else:
            print(f"  --keep: left in DB (video_id={video_id})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", default="short",
                        choices=sorted(_TOPICS.keys()))
    parser.add_argument("--keep", action="store_true",
                        help="do not delete the seeded video on exit")
    args = parser.parse_args(argv)
    return run(args.format, keep=args.keep)


if __name__ == "__main__":
    raise SystemExit(main())
