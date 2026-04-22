"""Telegram webhook handler for conversational agent.

Run as: python -m mesa.webhook
Or integrate with a web server (Flask, FastAPI, etc.)
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from mesa.config import get_settings
from mesa.conversational import handle_message

log = logging.getLogger(__name__)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming Telegram messages."""
    if not update.message or not update.message.text:
        return
    
    user = update.message.from_user
    msg = update.message.text.strip()
    
    log.info(f"Message from {user.first_name} (@{user.username}): {msg}")
    
    # Route to conversational prospector
    try:
        handle_message(msg)
    except Exception as e:
        log.error(f"Handler error: {e}", exc_info=True)
        await update.message.reply_text(f"⚠️ Error: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start command."""
    await update.message.reply_text(
        "🔭 Mesa Prospector Agent\n\n"
        "I scan for cash-secured put opportunities.\n\n"
        "*Try:*\n"
        "  find under $50\n"
        "  screen for high IV\n"
        "  check DDOG NFLX\n"
        "  top 5 with IV above 40%\n"
        "  between $30 and $80\n"
        "  show me prospects, no earnings\n",
        parse_mode="Markdown"
    )


async def main() -> None:
    """Start the bot."""
    settings = get_settings()
    
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not configured")
    
    # Create the Application
    app = Application.builder().token(settings.telegram_bot_token).build()
    
    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    log.info("Mesa Prospector bot started. Waiting for messages...")
    
    # Start polling
    await app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    asyncio.run(main())
