"""Natural-language parsing — each case here was wrong, or untested, in the old parser."""
from __future__ import annotations

import unittest

from mesa.conversational import HELP_TEXT, parse_criteria, respond


class IvIsNotAPriceTest(unittest.TestCase):
    def test_iv_above_pct_is_not_read_as_price_or_ticker(self) -> None:
        # Old parser: price_min=40 (from "above 40") and a ticker called "IV".
        c = parse_criteria("top 5 with IV above 40%")
        self.assertEqual(c["iv_threshold"], 0.40)
        self.assertEqual((c["price_min"], c["price_max"]), (50.0, 110.0))
        self.assertEqual(c["watchlist"], [])
        self.assertEqual(c["limit"], 5)

    def test_iv_forms(self) -> None:
        self.assertEqual(parse_criteria("find IV over 55")["iv_threshold"], 0.55)
        self.assertEqual(parse_criteria("scan iv 0.45")["iv_threshold"], 0.45)
        self.assertEqual(parse_criteria("find high iv")["iv_threshold"], 0.40)
        self.assertEqual(parse_criteria("find puts")["iv_threshold"], 0.0)


class PriceBandTest(unittest.TestCase):
    def test_under_keeps_default_floor(self) -> None:
        c = parse_criteria("find quality puts under $80")
        self.assertEqual((c["price_min"], c["price_max"]), (50.0, 80.0))

    def test_under_below_the_floor_drops_the_floor(self) -> None:
        c = parse_criteria("under 30")
        self.assertEqual((c["price_min"], c["price_max"]), (0.0, 30.0))

    def test_between_variants(self) -> None:
        for msg in ("screen between 60 and 90", "scan $60 to $90", "between $90 and $60"):
            c = parse_criteria(msg)
            self.assertEqual((c["price_min"], c["price_max"]), (60.0, 90.0), msg)

    def test_over_opens_the_ceiling(self) -> None:
        c = parse_criteria("show me over $150")
        self.assertEqual(c["price_min"], 150.0)
        self.assertGreater(c["price_max"], 1000)

    def test_percentages_are_never_prices(self) -> None:
        c = parse_criteria("find puts, premium above 3%")
        self.assertEqual((c["price_min"], c["price_max"]), (50.0, 110.0))


class TickerTest(unittest.TestCase):
    def test_uppercase_and_lowercase_tickers(self) -> None:
        self.assertEqual(parse_criteria("check DDOG NFLX")["watchlist"], ["DDOG", "NFLX"])
        self.assertEqual(parse_criteria("check ddog nflx")["watchlist"], ["DDOG", "NFLX"])
        self.assertEqual(parse_criteria("how's JPM")["watchlist"], ["JPM"])

    def test_bare_tickers_count_as_a_request(self) -> None:
        self.assertEqual(parse_criteria("DDOG NFLX")["watchlist"], ["DDOG", "NFLX"])

    def test_command_words_are_not_tickers(self) -> None:
        self.assertEqual(parse_criteria("FIND TOP 5 PUTS UNDER 80")["watchlist"], [])
        self.assertEqual(parse_criteria("scan now")["watchlist"], [])  # "now" is not ServiceNow

    def test_named_tickers_beat_universe_scan(self) -> None:
        self.assertFalse(parse_criteria("scan all KO")["universe"])


class IntentTest(unittest.TestCase):
    def test_universe_words(self) -> None:
        self.assertTrue(parse_criteria("scan all")["universe"])
        self.assertTrue(parse_criteria("find wide")["universe"])
        self.assertFalse(parse_criteria("scan")["universe"])

    def test_limit_is_clamped(self) -> None:
        self.assertEqual(parse_criteria("top 99")["limit"], 10)
        self.assertEqual(parse_criteria("top 0")["limit"], 1)
        self.assertEqual(parse_criteria("find puts")["limit"], 3)

    def test_chitchat_gets_help(self) -> None:
        self.assertIsNone(parse_criteria("hello there"))
        self.assertEqual(respond("hello there"), HELP_TEXT)

    def test_earnings_gate_is_always_on(self) -> None:
        self.assertTrue(parse_criteria("find puts")["exclude_earnings"])


if __name__ == "__main__":
    unittest.main()
