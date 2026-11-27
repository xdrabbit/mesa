"""Telegram bot (polling mode) — natural-language front end to the prospector.

Run with `mesa bot` (or `python -m mesa.webhook`). The bot only answers the chat configured in
TELEGRAM_CHAT_ID; everyone else is ignored.

Design notes (each one was a bug):
  * `Application.run_polling()` is synchronous and owns the event loop — it must not be awaited
    inside `asyncio.run()`.
  * Scans are blocking (yfinance), so they run in a worker thread; the event loop stays free.
  * Replies go through the incoming message, not `telegram_send.send()` (which is for cron).
"""
from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from mesa.config import get_settings
from mesa.conversational import HELP_TEXT, parse_criteria, run_screen
from mesa.telegram_send import split_message

log = logging.getLogger(__name__)


def _authorized(update: Update) -> bool:
    chat = update.effective_chat
    allowed = get_settings().telegram_chat_id.strip()
    return bool(chat and allowed and str(chat.id) == allowed)


async def _reply(message, text: str) -> None:
    for chunk in split_message(text):
        try:
            await message.reply_text(chunk, parse_mode="Markdown")
        except BadRequest as e:  # unbalanced Markdown etc. — resend as plain text
            log.warning("Markdown rejected (%s); resending as plain text", e)
            await message.reply_text(chunk)


async def on_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or not _authorized(update):
        return
    await _reply(update.effective_message, HELP_TEXT)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.text:
        return
    if not _authorized(update):
        chat_id = update.effective_chat.id if update.effective_chat else "?"
        log.warning("Ignoring message from unauthorized chat %s", chat_id)
        return

    log.info("Message: %s", message.text)
    criteria = parse_criteria(message.text)
    if criteria is None:
        await _reply(message, HELP_TEXT)
        return

    # One scan at a time: concurrent scans just fight over Yahoo's rate limit.
    lock: asyncio.Lock = context.bot_data.setdefault("scan_lock", asyncio.Lock())
    if lock.locked():
        await _reply(message, "⏳ A scan is already running — give it a minute.")
        return

    async with lock:
        if criteria["universe"]:
            await _reply(message, "🔍 Scanning the full index list — this takes a few minutes…")
        else:
            await _reply(message, "🔍 Scanning…")
        try:
            text = await asyncio.to_thread(run_screen, criteria)
        except Exception as e:
            log.exception("Scan failed")
            text = f"⚠️ Scan failed ({type(e).__name__}). Check the bot logs."
        await _reply(message, text)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled error while processing update", exc_info=context.error)


def build_application() -> Application:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not configured (see .env.example)")
    if not settings.telegram_chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID not configured — the bot only answers that chat")

    app = Application.builder().token(settings.telegram_bot_token).concurrent_updates(True).build()
    app.add_handler(CommandHandler(["start", "help"], on_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)
    return app


def main() -> None:
    """Start the bot. Blocks until interrupted (run_polling owns the event loop)."""
    app = build_application()
    log.info("Mesa Prospector bot started. Waiting for messages…")
    # drop_pending_updates: don't replay messages that piled up while the bot was down.
    app.run_polling(allowed_updates=[Update.MESSAGE], drop_pending_updates=True)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    main()
