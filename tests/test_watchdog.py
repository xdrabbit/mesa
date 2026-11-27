"""Watchdog must not raise a 'close for profit' alert off a dead (bid=ask=0) quote."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from mesa import watchdog
from mesa.config import Settings
from mesa.models import Position


def position() -> Position:
    return Position("TEST", 90.0, "2099-01-15", "put", "short", premium_collected=300.0)


def chain(bid: float, ask: float) -> dict:
    puts = pd.DataFrame([{"strike": 90.0, "bid": bid, "ask": ask}])
    return {"puts": puts, "calls": puts.iloc[0:0]}


class CloseAlertTest(unittest.TestCase):
    def alerts(self, bid: float, ask: float) -> list[str]:
        with patch.object(watchdog, "get_price", return_value=100.0), \
                patch.object(watchdog, "get_option_chain", return_value=chain(bid, ask)):
            return watchdog._check(position())[0]

    def test_dead_quote_raises_no_close_alert(self) -> None:
        self.assertFalse([a for a in self.alerts(0.0, 0.0) if "Consider closing" in a])

    def test_cheap_live_quote_does_raise_it(self) -> None:
        # mid $0.30 → $30 value < 20% of $300 collected
        self.assertTrue([a for a in self.alerts(0.25, 0.35) if "Consider closing" in a])

    def test_pricey_live_quote_does_not(self) -> None:
        self.assertFalse([a for a in self.alerts(2.0, 2.2) if "Consider closing" in a])


class ConfigTest(unittest.TestCase):
    def test_unrelated_keys_in_dotenv_do_not_crash_settings(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("TELEGRAM_BOT_TOKEN=1:abc\nTELEGRAM_CHAT_ID=5\nSOMETHING_ELSE=1\n")
            s = Settings(_env_file=str(env))
        self.assertEqual((s.telegram_bot_token, s.telegram_chat_id), ("1:abc", "5"))


if __name__ == "__main__":
    unittest.main()
