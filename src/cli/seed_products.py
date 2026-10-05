"""CSV → products. Идемпотентно: UNIQUE(affiliate_url) + ON CONFLICT DO NOTHING.

CSV формат (UTF-8, заголовки обязательны):
    name,category,affiliate_url,program,commission_pct,price_usd

  • name           — отображаемое имя продукта
  • category       — mouse | keyboard | monitor | chair | desk | mic | ...
  • affiliate_url  — финальная аффилиатная ссылка
  • program        — amazon | impact:<brand> | direct:<brand>
  • commission_pct — числом (например 5.0); опционально
  • price_usd      — числом; опционально

Использование:
    python -m src.cli.seed_products --csv ./samples/products.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from src.core.config import MissingConfigError, require
from src.core.db import connection

REQUIRED_COLUMNS = {"name", "category", "affiliate_url", "program"}
OPTIONAL_NUMERIC = ("commission_pct", "price_usd")


class CsvError(ValueError):
    pass


def parse_csv(path: Path) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not REQUIRED_COLUMNS.issubset(reader.fieldnames or []):
            raise CsvError(
                f"CSV missing required columns. Need {sorted(REQUIRED_COLUMNS)}, "
                f"got {reader.fieldnames}"
            )
        for n, row in enumerate(reader, 2):  # n=2 учитывает заголовок
            name = (row.get("name") or "").strip()
            if not name:
                continue
            cat = (row.get("category") or "").strip()
            url = (row.get("affiliate_url") or "").strip()
            prog = (row.get("program") or "").strip()
            if not (cat and url and prog):
                raise CsvError(f"row {n}: empty category/affiliate_url/program")
            rec: dict[str, object] = {
                "name": name, "category": cat,
                "affiliate_url": url, "program": prog,
            }
            for key in OPTIONAL_NUMERIC:
                raw = (row.get(key) or "").strip()
                rec[key] = float(raw) if raw else None
            out.append(rec)
    return out


def upsert(records: list[dict[str, object]]) -> tuple[int, int]:
    """Возвращает (inserted, skipped). ON CONFLICT(affiliate_url) DO NOTHING."""
    if not records:
        return 0, 0
    sql = (
        "INSERT INTO products "
        "(name, category, affiliate_url, program, commission_pct, price_usd) "
        "VALUES (%(name)s, %(category)s, %(affiliate_url)s, %(program)s, "
        "        %(commission_pct)s, %(price_usd)s) "
        "ON CONFLICT (affiliate_url) DO NOTHING "
        "RETURNING id"
    )
    inserted = 0
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                for rec in records:
                    cur.execute(sql, rec)
                    if cur.fetchone():
                        inserted += 1
    return inserted, len(records) - inserted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, type=Path)
    args = parser.parse_args(argv)

    if not args.csv.is_file():
        print(f"[seed] csv not found: {args.csv}", file=sys.stderr)
        return 2

    try:
        require("epic1")
    except MissingConfigError as exc:
        print(f"[seed] config error: {exc}", file=sys.stderr)
        return 2

    try:
        rows = parse_csv(args.csv)
    except CsvError as exc:
        print(f"[seed] {exc}", file=sys.stderr)
        return 1

    inserted, skipped = upsert(rows)
    print(f"[seed] read={len(rows)} inserted={inserted} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
