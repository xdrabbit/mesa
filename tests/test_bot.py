"""Telegram bot + delivery. The first two tests reproduce the two bugs that kept the bot dead:
`await app.run_polling()` inside asyncio.run(), and send() calling asyncio.run() in a live loop."""
from __future__ import annotations

import asyncio
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.error import BadRequest

from mesa import telegram_send, webhook
from mesa.config import Settings
from mesa.conversational import HELP_TEXT

CHAT_ID = 4242


def settings(token: str = "123:abc", chat: str = str(CHAT_ID)) -> Settings:
    return Settings(_env_file=None, telegram_bot_token=token, telegram_chat_id=chat)


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str, parse_mode: str | None = None) -> None:
        self.replies.append(text)


def make_update(text: str, chat_id: int = CHAT_ID):
    msg = FakeMessage(text)
    return SimpleNamespace(effective_message=msg, effective_chat=SimpleNamespace(id=chat_id)), msg


def make_context():
    return SimpleNamespace(bot_data={})


class StartupAndDeliveryTest(unittest.TestCase):
    def test_main_is_synchronous_because_run_polling_owns_the_loop(self) -> None:
        # Bug A: `await app.run_polling()` inside asyncio.run() → "Cannot close a running event loop".
        self.assertFalse(inspect.iscoroutinefunction(webhook.main))
        app = MagicMock()
        with patch.object(webhook, "build_application", return_value=app):
            webhook.main()
        app.run_polling.assert_called_once()
        self.assertTrue(app.run_polling.call_args.kwargs["drop_pending_updates"])

    def test_send_works_from_inside_a_running_loop(self) -> None:
        # Bug B: send() did asyncio.run() unconditionally → "cannot be called from a running event loop".
        sent: list[str] = []

        async def fake_send_async(token: str, chat_id: str, message: str) -> None:
            sent.append(message)

        async def handler() -> None:
            telegram_send.send("from inside the loop")

        with patch.object(telegram_send, "get_settings", return_value=settings()), \
                patch.object(telegram_send, "_send_async", fake_send_async):
            asyncio.run(handler())
            telegram_send.send("from plain code")
        self.assertEqual(sent, ["from inside the loop", "from plain code"])

    def test_send_prints_when_unconfigured(self) -> None:
        with patch.object(telegram_send, "get_settings", return_value=settings(token="", chat="")), \
                patch("builtins.print") as fake_print:
            telegram_send.send("hello")
        fake_print.assert_called_once_with("hello")

    def test_build_application_refuses_to_start_without_a_chat_id(self) -> None:
        with patch.object(webhook, "get_settings", return_value=settings(chat="")):
            with self.assertRaisesRegex(RuntimeError, "TELEGRAM_CHAT_ID"):
                webhook.build_application()
        with patch.object(webhook, "get_settings", return_value=settings(token="")):
            with self.assertRaisesRegex(RuntimeError, "TELEGRAM_BOT_TOKEN"):
                webhook.build_application()

    def test_build_application_registers_handlers(self) -> None:
        with patch.object(webhook, "get_settings", return_value=settings()):
            app = webhook.build_application()
        self.assertGreaterEqual(len(app.handlers[0]), 2)


class SplitMessageTest(unittest.TestCase):
    def test_short_message_untouched(self) -> None:
        self.assertEqual(telegram_send.split_message("hi"), ["hi"])

    def test_long_message_splits_on_lines_within_limit(self) -> None:
        text = "\n".join(f"line {i:04d} " + "x" * 40 for i in range(300))
        chunks = telegram_send.split_message(text, limit=500)
        self.assertTrue(all(len(c) <= 500 for c in chunks))
        self.assertEqual("\n".join(chunks), text)

    def test_single_giant_line_is_hard_split(self) -> None:
        chunks = telegram_send.split_message("y" * 1200, limit=500)
        self.assertEqual([len(c) for c in chunks], [500, 500, 200])


class HandlerTest(unittest.TestCase):
    def run_handler(self, text: str, chat_id: int = CHAT_ID, run_screen=None, context=None):
        update, msg = make_update(text, chat_id)
        run_screen = run_screen or MagicMock(return_value="RESULTS")
        with patch.object(webhook, "get_settings", return_value=settings()), \
                patch.object(webhook, "run_screen", run_screen):
            asyncio.run(webhook.on_message(update, context or make_context()))
        return msg, run_screen

    def test_authorised_chat_gets_ack_then_results(self) -> None:
        msg, run_screen = self.run_handler("top 3 under $80")
        self.assertEqual(msg.replies, ["🔍 Scanning…", "RESULTS"])
        criteria = run_screen.call_args.args[0]
        self.assertEqual(criteria["price_max"], 80.0)

    def test_full_universe_scan_warns_it_is_slow(self) -> None:
        msg, _ = self.run_handler("scan all")
        self.assertIn("few minutes", msg.replies[0])

    def test_unauthorised_chat_is_ignored_completely(self) -> None:
        msg, run_screen = self.run_handler("scan", chat_id=999)
        self.assertEqual(msg.replies, [])
        run_screen.assert_not_called()

    def test_chitchat_gets_help_without_scanning(self) -> None:
        msg, run_screen = self.run_handler("hello there")
        self.assertEqual(msg.replies, [HELP_TEXT])
        run_screen.assert_not_called()

    def test_scan_failure_is_reported_not_raised(self) -> None:
        msg, _ = self.run_handler("scan", run_screen=MagicMock(side_effect=RuntimeError("boom")))
        self.assertEqual(msg.replies[0], "🔍 Scanning…")
        self.assertIn("Scan failed (RuntimeError)", msg.replies[1])

    def test_second_request_while_scanning_is_told_to_wait(self) -> None:
        async def scenario():
            ctx = make_context()
            lock = ctx.bot_data.setdefault("scan_lock", asyncio.Lock())
            update, msg = make_update("scan")
            async with lock:  # a scan is "running"
                await webhook.on_message(update, ctx)
            return msg

        with patch.object(webhook, "get_settings", return_value=settings()), \
                patch.object(webhook, "run_screen") as run_screen:
            msg = asyncio.run(scenario())
        self.assertEqual(len(msg.replies), 1)
        self.assertIn("already running", msg.replies[0])
        run_screen.assert_not_called()

    def test_help_command_only_for_authorised_chat(self) -> None:
        for chat_id, expected in ((CHAT_ID, [HELP_TEXT]), (7, [])):
            update, msg = make_update("/help", chat_id)
            with patch.object(webhook, "get_settings", return_value=settings()):
                asyncio.run(webhook.on_help(update, make_context()))
            self.assertEqual(msg.replies, expected)

    def test_markdown_rejection_falls_back_to_plain_text(self) -> None:
        message = SimpleNamespace(reply_text=AsyncMock(side_effect=[BadRequest("can't parse entities"), None]))
        asyncio.run(webhook._reply(message, "bad_*markdown"))
        self.assertEqual(message.reply_text.await_count, 2)
        self.assertEqual(message.reply_text.await_args_list[1].kwargs, {})  # second call: no parse_mode


if __name__ == "__main__":
    unittest.main()
