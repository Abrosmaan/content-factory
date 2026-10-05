"""Регистрация меню команд через Telegram Bot API setMyCommands.

Вызывается из bot.main один раз при старте — Telegram запоминает
список глобально для бота, можно было бы делать через @BotFather, но
держим в коде, чтобы не зависеть от ручных действий при ротации.

Список синхронизирован с реальными handler'ами в `src.bot.handlers`.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BotCommand

log = logging.getLogger(__name__)

COMMANDS: list[tuple[str, str]] = [
    ("start",    "запустить бота"),
    ("costs",    "дашборд расходов"),
    ("status",   "статус системы и очереди"),
    ("switches", "kill switches (пайплайн / YT / дистрибуция)"),
    ("formats",  "форматы контента (on/off)"),
    ("topics",   "показать топ pending тем"),
    ("note",     "добавить идею для видео"),
    ("help",     "список команд"),
]


async def set_my_commands(bot: Bot) -> None:
    cmds = [BotCommand(command=c, description=d) for c, d in COMMANDS]
    try:
        await bot.set_my_commands(cmds)
        log.info("setMyCommands: %d commands registered", len(cmds))
    except Exception:
        log.warning("setMyCommands failed", exc_info=True)


__all__ = ["COMMANDS", "set_my_commands"]
