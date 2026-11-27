"""Telegram delivery for cron jobs and scripts.

`send()` is synchronous and safe to call from plain code *or* from inside a running event loop
(the old version called asyncio.run() unconditionally, which raises inside the bot's loop).
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from telegram import Bot
from telegram.error import BadRequest

from mesa.config import get_settings

log = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 4096  # Telegram's hard per-message limit


def split_message(message: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """Split on line boundaries so no chunk exceeds Telegram's limit."""
    if len(message) <= limit:
        return [message]
    chunks: list[str] = []
    current = ""
    for line in message.split("\n"):
        while len(line) > limit:  # a single monster line: hard-split it
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def send(message: str) -> None:
    """Send a Telegram message synchronously."""
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        log.warning("Telegram not configured — printing to stdout instead")
        print(message)
        return

    coro = _send_async(settings.telegram_bot_token, settings.telegram_chat_id, message)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
    else:
        # Called from inside a running loop: asyncio.run() would raise, so use a helper thread.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(asyncio.run, coro).result()


async def _send_async(token: str, chat_id: str, message: str) -> None:
    async with Bot(token=token) as bot:
        for chunk in split_message(message):
            try:
                await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="Markdown")
            except BadRequest as e:  # unbalanced Markdown etc. — resend as plain text
                log.warning("Markdown rejected (%s); resending as plain text", e)
                await bot.send_message(chat_id=chat_id, text=chunk)
