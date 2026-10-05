"""Запуск Telegram-бота (polling).

Используется как самостоятельная точка входа:
    python -m src.bot.main
А также подключается из общего entrypoint (E4.4 scheduler).
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from src.bot.alerts import send_alert
from src.bot.handlers import router
from src.bot.menu import set_my_commands
from src.bot.review import router as review_router
from src.core.config import require
from src.core.retry import set_alert_sink

log = logging.getLogger(__name__)


async def run_bot() -> None:
    settings = require("epic1")
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    dp.include_router(review_router)

    set_alert_sink(send_alert)

    # APScheduler в том же event loop. start() безопасен без jobs тоже.
    from src.scheduler import setup_scheduler
    scheduler = setup_scheduler()
    scheduler.start()
    log.info("scheduler started")

    # setMyCommands — обновляем меню /-команд в клиенте Telegram.
    await set_my_commands(bot)

    log.info("bot starting polling")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        log.info("interrupted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
