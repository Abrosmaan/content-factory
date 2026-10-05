"""Восстановление из R2-бэкапа в указанный Postgres-DSN.

Использование:
    python -m src.cli.restore --key backups/2026-06-12.sql.gz \
        --dsn 'postgresql://postgres:...@db.example/postgres'
    # либо последний по дате:
    python -m src.cli.restore --latest --dsn '...'

DSN не берём из ENV намеренно — restore идёт в чистую БД, не в боевую.
"""

from __future__ import annotations

import argparse
import gzip
import subprocess
import sys
import tempfile
from pathlib import Path

from src.cli.backup import BACKUP_PREFIX, list_backups
from src.core import r2
from src.core.config import require


def _latest_key(bucket: str) -> str | None:
    items = list_backups(bucket)
    return items[-1][0] if items else None


def restore(*, key: str, bucket: str, dsn: str) -> None:
    cli = r2.client()
    with tempfile.NamedTemporaryFile(suffix=".sql.gz", delete=False) as f:
        local = Path(f.name)
    try:
        cli.download_file(bucket, key, str(local))
        with gzip.open(local, "rb") as gz, tempfile.NamedTemporaryFile(
                suffix=".sql", delete=False) as plain:
            plain.write(gz.read())
            plain_path = Path(plain.name)
        try:
            subprocess.check_call(["psql", dsn, "-f", str(plain_path)])
        finally:
            plain_path.unlink(missing_ok=True)
    finally:
        local.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--key", help="explicit R2 backup key (backups/YYYY-MM-DD.sql.gz)")
    src.add_argument("--latest", action="store_true")
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args(argv)
    s = require("epic1")
    bucket = s.R2_BUCKET_RENDERS
    key = args.key
    if args.latest:
        key = _latest_key(bucket)
        if not key:
            print("[restore] no backups in bucket", file=sys.stderr)
            return 1
    restore(key=key, bucket=bucket, dsn=args.dsn)
    print(f"[restore] applied {key} → {args.dsn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
