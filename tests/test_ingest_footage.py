"""Юнит-тесты CLI ingest_footage: парсинг CSV, выбор файлов, R2-ключ."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.cli.ingest_footage import collect_items, make_r2_key, parse_csv


def test_parse_csv_basic(tmp_path: Path):
    csv = tmp_path / "tags.csv"
    csv.write_text(
        "filename,game,tags\n"
        "cs2_combat_01.mp4,cs2,combat;round_win\n"
        "cs2_menu_01.mp4,cs2,menu\n",
        encoding="utf-8",
    )
    m = parse_csv(csv)
    assert m["cs2_combat_01.mp4"] == ("cs2", ["combat", "round_win"])
    assert m["cs2_menu_01.mp4"] == ("cs2", ["menu"])


def test_parse_csv_bom_tolerant(tmp_path: Path):
    csv = tmp_path / "tags.csv"
    csv.write_bytes(b"\xef\xbb\xbffilename,game,tags\nfoo.mp4,cs2,combat\n")
    assert "foo.mp4" in parse_csv(csv)


def test_parse_csv_missing_column(tmp_path: Path):
    csv = tmp_path / "bad.csv"
    csv.write_text("filename,game\nfoo.mp4,cs2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_csv(csv)


def test_collect_items_skips_unmapped(tmp_path: Path):
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.mp4").write_bytes(b"x")
    mapping = {"a.mp4": ("cs2", ["combat"])}
    items = collect_items(tmp_path, mapping)
    names = [it.path.name for it in items]
    assert names == ["a.mp4"]


def test_make_r2_key():
    assert make_r2_key("cs2", "x.mp4") == "cs2/x.mp4"
