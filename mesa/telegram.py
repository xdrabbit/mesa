from __future__ import annotations

import asyncio
import logging

from telegram import Bot

from mesa.config import get_settings

log = logging.getLogger(__name__)


def send(message: str) -> None:
    """Send a Telegram message synchronously (safe to call from cron scripts)."""
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        log.warning("Telegram not configured — printing to stdout instead")
        print(message)
        return
    asyncio.run(_send_async(settings.telegram_bot_token, settings.telegram_chat_id, message))


async def _send_async(token: str, chat_id: str, message: str) -> None:
    bot = Bot(token=token)
    await bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
