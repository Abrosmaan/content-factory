"""Юнит-тесты seed_products CLI: парсинг CSV."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.cli import seed_products as sp


def test_parse_csv_minimal(tmp_path: Path):
    csv = tmp_path / "p.csv"
    csv.write_text(
        "name,category,affiliate_url,program,commission_pct,price_usd\n"
        "Mouse A,mouse,https://x/a,amazon,5.0,89\n"
        "KB B,keyboard,https://x/b,impact:logitech,7,159\n",
        encoding="utf-8",
    )
    out = sp.parse_csv(csv)
    assert len(out) == 2
    assert out[0]["name"] == "Mouse A"
    assert out[0]["commission_pct"] == 5.0
    assert out[0]["price_usd"] == 89.0
    assert out[1]["program"] == "impact:logitech"


def test_parse_csv_optional_numerics_empty(tmp_path: Path):
    csv = tmp_path / "p.csv"
    csv.write_text(
        "name,category,affiliate_url,program,commission_pct,price_usd\n"
        "X,mouse,https://x/a,amazon,,\n",
        encoding="utf-8",
    )
    out = sp.parse_csv(csv)
    assert out[0]["commission_pct"] is None
    assert out[0]["price_usd"] is None


def test_parse_csv_rejects_missing_column(tmp_path: Path):
    csv = tmp_path / "p.csv"
    csv.write_text(
        "name,category,affiliate_url\nA,mouse,https://x\n", encoding="utf-8",
    )
    with pytest.raises(sp.CsvError):
        sp.parse_csv(csv)


def test_parse_csv_rejects_empty_fields(tmp_path: Path):
    csv = tmp_path / "p.csv"
    csv.write_text(
        "name,category,affiliate_url,program,commission_pct,price_usd\n"
        "X,,https://x,amazon,5,99\n",
        encoding="utf-8",
    )
    with pytest.raises(sp.CsvError):
        sp.parse_csv(csv)


def test_parse_csv_skips_blank_rows(tmp_path: Path):
    csv = tmp_path / "p.csv"
    csv.write_text(
        "name,category,affiliate_url,program,commission_pct,price_usd\n"
        ",mouse,https://x,amazon,5,99\n"
        "Real,mouse,https://y,amazon,5,99\n",
        encoding="utf-8",
    )
    out = sp.parse_csv(csv)
    assert [r["name"] for r in out] == ["Real"]
