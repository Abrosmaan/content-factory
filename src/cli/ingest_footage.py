"""Загрузка геймплейного футажа в R2 + регистрация в таблице `footage`.

Использование:
    python -m src.cli.ingest_footage --dir ./samples --csv ./samples/tags.csv

CSV (без BOM, заголовки обязательны):
    filename,game,tags
    cs2_combat_01.mp4,cs2,combat;round_win
    cs2_menu_01.mp4,cs2,menu

`tags` — `;`-разделённый список. ffprobe должен быть в PATH.

Идемпотентность: при повторном запуске уже залитый файл не дублируется
(UNIQUE(r2_key) + ON CONFLICT DO NOTHING).
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from src.core import r2
from src.core.config import MissingConfigError, require
from src.core.db import connection


@dataclass
class Probe:
    duration_sec: float
    resolution: str  # WxH


@dataclass
class Item:
    path: Path
    game: str
    tags: list[str]


def parse_csv(csv_path: Path) -> dict[str, tuple[str, list[str]]]:
    out: dict[str, tuple[str, list[str]]] = {}
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"filename", "game", "tags"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(
                f"CSV must have columns {sorted(required)}, got {reader.fieldnames}"
            )
        for row in reader:
            filename = row["filename"].strip()
            if not filename:
                continue
            game = row["game"].strip()
            tags = [t.strip() for t in row["tags"].split(";") if t.strip()]
            out[filename] = (game, tags)
    return out


def ffprobe(path: Path) -> Probe:
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe not found in PATH")
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True)
    data = json.loads(out)
    stream = (data.get("streams") or [{}])[0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    duration = float(data.get("format", {}).get("duration") or 0.0)
    if not (width and height and duration):
        raise ValueError(f"ffprobe gave incomplete data for {path}: {data}")
    return Probe(duration_sec=duration, resolution=f"{width}x{height}")


def make_r2_key(game: str, filename: str) -> str:
    return f"{game}/{filename}"


def upload_and_register(bucket: str, item: Item, probe: Probe) -> tuple[str, bool]:
    """Возвращает (r2_key, inserted_bool)."""
    key = make_r2_key(item.game, item.path.name)
    r2.upload_file(bucket, key, str(item.path), content_type="video/mp4")
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO footage (r2_key, game, tags, duration_sec, resolution) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (r2_key) DO NOTHING "
                    "RETURNING id",
                    (key, item.game, item.tags, probe.duration_sec, probe.resolution),
                )
                inserted = cur.fetchone() is not None
    return key, inserted


def collect_items(dir_path: Path, mapping: dict[str, tuple[str, list[str]]]) -> list[Item]:
    items: list[Item] = []
    for mp4 in sorted(dir_path.glob("*.mp4")):
        meta = mapping.get(mp4.name)
        if meta is None:
            print(f"[ingest] skip {mp4.name}: no row in CSV")
            continue
        game, tags = meta
        if not game:
            print(f"[ingest] skip {mp4.name}: empty game")
            continue
        items.append(Item(path=mp4, game=game, tags=tags))
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True, type=Path)
    parser.add_argument("--csv", required=True, type=Path)
    args = parser.parse_args(argv)

    if not args.dir.is_dir():
        print(f"--dir not a directory: {args.dir}", file=sys.stderr)
        return 2
    if not args.csv.is_file():
        print(f"--csv not found: {args.csv}", file=sys.stderr)
        return 2

    try:
        settings = require("epic1")
    except MissingConfigError as exc:
        print(f"[ingest] config error: {exc}", file=sys.stderr)
        return 2

    mapping = parse_csv(args.csv)
    items = collect_items(args.dir, mapping)
    if not items:
        print("[ingest] nothing to upload")
        return 0

    bucket = settings.R2_BUCKET_FOOTAGE
    uploaded = 0
    skipped = 0
    for item in items:
        try:
            probe = ffprobe(item.path)
        except Exception as exc:
            print(f"[ingest] {item.path.name}: ffprobe failed — {exc}", file=sys.stderr)
            continue
        key, inserted = upload_and_register(bucket, item, probe)
        if inserted:
            uploaded += 1
            print(f"[ingest] uploaded {key} ({probe.resolution}, {probe.duration_sec:.1f}s)")
        else:
            skipped += 1
            print(f"[ingest] skipped (already in DB) {key}")
    print(f"[ingest] done: uploaded={uploaded} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
