"""pg_dump → gzip → R2 (R2_BUCKET_RENDERS/backups/YYYY-MM-DD.sql.gz).

Запуск:
    python -m src.cli.backup            # текущая дата
    python -m src.cli.backup --keep N   # удалить дампы старше N дней
                                        # (default 30)

Подключается в scheduler (E4.4) на ежедневный cron 04:00 UTC.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

from src.core import r2
from src.core.config import MissingConfigError, require

log = logging.getLogger(__name__)

BACKUP_PREFIX = "backups/"


def _date_key(date: dt.date) -> str:
    return f"{BACKUP_PREFIX}{date.isoformat()}.sql.gz"


def dump_to(path: Path, dsn: str) -> None:
    with path.open("wb") as gz_out, gzip.open(gz_out, "wb") as gz:
        proc = subprocess.Popen(
            ["pg_dump", "--no-owner", "--no-privileges", dsn],
            stdout=subprocess.PIPE,
        )
        assert proc.stdout is not None
        for chunk in iter(lambda: proc.stdout.read(65536), b""):
            gz.write(chunk)
        ret = proc.wait()
    if ret != 0:
        raise RuntimeError(f"pg_dump exited with code {ret}")


def upload(local: Path, bucket: str, key: str) -> None:
    r2.upload_file(bucket, key, str(local), content_type="application/gzip")


def list_backups(bucket: str) -> list[tuple[str, dt.datetime, int]]:
    cli = r2.client()
    resp = cli.list_objects_v2(Bucket=bucket, Prefix=BACKUP_PREFIX)
    out: list[tuple[str, dt.datetime, int]] = []
    for obj in resp.get("Contents", []) or []:
        out.append((obj["Key"], obj["LastModified"], obj["Size"]))
    return sorted(out, key=lambda r: r[1])


def prune_older_than(bucket: str, days: int) -> int:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    cli = r2.client()
    removed = 0
    for key, modified, _ in list_backups(bucket):
        if modified < cutoff:
            cli.delete_object(Bucket=bucket, Key=key)
            removed += 1
    return removed


def run_once(*, keep_days: int = 30) -> dict:
    s = require("epic1")
    today = dt.date.today()
    key = _date_key(today)
    with tempfile.NamedTemporaryFile(suffix=".sql.gz", delete=False) as f:
        local = Path(f.name)
    try:
        dump_to(local, s.SUPABASE_DB_URL)
        upload(local, s.R2_BUCKET_RENDERS, key)
        size = local.stat().st_size
    finally:
        local.unlink(missing_ok=True)
    pruned = prune_older_than(s.R2_BUCKET_RENDERS, keep_days)
    log.info("backup: key=%s size=%d pruned=%d", key, size, pruned)
    return {"key": key, "size": size, "pruned": pruned}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", type=int, default=30)
    args = parser.parse_args(argv)
    try:
        require("epic1")
    except MissingConfigError as exc:
        print(f"[backup] config error: {exc}", file=sys.stderr)
        return 2
    res = run_once(keep_days=args.keep)
    print(f"[backup] done: {res}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
