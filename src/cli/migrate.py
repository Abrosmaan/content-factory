"""Применение SQL-миграций из ./migrations к Supabase Postgres.

Идемпотентно: журнал в schema_migrations(version PRIMARY KEY).
Файлы выполняются в лексикографическом порядке; каждый — в собственной
транзакции, что согласуется с DO-блоками в 001_initial_schema.sql.

Использование:
    python -m src.cli.migrate           # применить все pending
    python -m src.cli.migrate --status  # показать статус
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

from src.core.config import MissingConfigError, require, project_root


def _migration_files() -> list[Path]:
    mig_dir = project_root() / "migrations"
    return sorted(p for p in mig_dir.glob("*.sql") if p.is_file())


def _applied_versions(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}


def _ensure_meta(conn: psycopg.Connection) -> None:
    meta_sql = (project_root() / "migrations" / "000_meta.sql").read_text()
    with conn.cursor() as cur:
        cur.execute(meta_sql)
    conn.commit()


def apply_all(dsn: str) -> list[str]:
    files = _migration_files()
    if not files:
        return []
    with psycopg.connect(dsn, autocommit=False) as conn:
        _ensure_meta(conn)
        applied = _applied_versions(conn)
        newly: list[str] = []
        for path in files:
            version = path.stem
            if version in applied:
                continue
            sql = path.read_text()
            print(f"[migrate] applying {version}")
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    cur.execute(
                        "INSERT INTO schema_migrations(version) VALUES (%s)",
                        (version,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            newly.append(version)
        return newly


def status(dsn: str) -> tuple[list[str], list[str]]:
    files = [p.stem for p in _migration_files()]
    with psycopg.connect(dsn) as conn:
        _ensure_meta(conn)
        applied = _applied_versions(conn)
    pending = [v for v in files if v not in applied]
    return sorted(applied), pending


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true", help="show migrations status")
    args = parser.parse_args(argv)

    try:
        settings = require("epic1")
    except MissingConfigError as exc:
        print(f"[migrate] config error: {exc}", file=sys.stderr)
        return 2

    dsn = settings.SUPABASE_DB_URL
    if args.status:
        applied, pending = status(dsn)
        print(f"applied ({len(applied)}):")
        for v in applied:
            print(f"  - {v}")
        print(f"pending ({len(pending)}):")
        for v in pending:
            print(f"  - {v}")
        return 0

    newly = apply_all(dsn)
    if not newly:
        print("[migrate] nothing to apply (DB up-to-date)")
    else:
        print(f"[migrate] applied {len(newly)}: {', '.join(newly)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
