"""Главный entrypoint контейнера content-factory.

На этапе эпика 1 запускает Telegram-бот (polling). Scheduler и все
воркеры подключатся в E4.4 — там main.py соберёт asyncio.gather из
бота, scheduler-цикла и поллингов воркеров.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from src.bot.main import run_bot
from src.core.config import MissingConfigError, get_settings


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


def main() -> int:
    try:
        settings = get_settings()
    except MissingConfigError as exc:
        print(f"[main] config error: {exc}", file=sys.stderr)
        return 2

    _setup_logging(settings.LOG_LEVEL)
    log = logging.getLogger("main")
    log.info("content-factory boot env=%s", settings.ENV)
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        log.info("interrupted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
