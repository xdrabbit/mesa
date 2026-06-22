"""Baseline (covered-call → cash-secured-put conversion + $50–$110 band) regression tests.

These cover the put-selection scan and the conversational natural-language price parsing that
were introduced by the CSP-conversion/$50–$110-band baseline. They live on this branch because
they exercise that baseline behavior. (The risk-hardening regression tests live on
`feat/prospector-risk-hardening`.)
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
import sys
import unittest

sys.modules.setdefault("telegram", SimpleNamespace(Bot=object))
sys.modules.setdefault("yfinance", SimpleNamespace(Ticker=object))
sys.modules.setdefault("mesa.telegram_send", SimpleNamespace(send=lambda message: None))
sys.modules.setdefault("mesa.models", SimpleNamespace(load_positions=lambda: []))
sys.modules.setdefault("mesa.universe", SimpleNamespace(load_universe=lambda: []))

from mesa import conversational, prospector


class FakeRows:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def iterrows(self):
        for idx, row in enumerate(self.rows):
            yield idx, row


class ProspectorPutSelectionTest(unittest.TestCase):
    def test_scores_cash_secured_puts_below_current_price(self) -> None:
        today = date(2026, 6, 7)
        chain = SimpleNamespace(
            calls=FakeRows([
                {
                    "strike": 110.0,
                    "bid": 2.0,
                    "ask": 2.2,
                    "openInterest": 200,
                    "impliedVolatility": 0.40,
                    "delta": 0.25,
                }
            ]),
            puts=FakeRows([
                {
                    "strike": 90.0,
                    "bid": 2.0,
                    "ask": 2.2,
                    "openInterest": 200,
                    "impliedVolatility": 0.40,
                    "delta": -0.25,
                }
            ]),
        )
        ticker = SimpleNamespace(
            options=["2026-07-17"],
            option_chain=lambda _: chain,
        )
        info = {"currentPrice": 100.0}

        candidates, miss = prospector._score_options(ticker, info, "TEST", True, today)

        self.assertIsNone(miss)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].strike, 90.0)
        self.assertGreater(candidates[0].annualized, 0)


class ConversationalCriteriaTest(unittest.TestCase):
    def test_parse_under_and_between_price_ranges(self) -> None:
        under = conversational.parse_criteria("find quality puts under $80")
        between = conversational.parse_criteria("screen between 60 and 90")

        self.assertEqual(under["price_min"], 50.0)
        self.assertEqual(under["price_max"], 80.0)
        self.assertEqual(between["price_min"], 60.0)
        self.assertEqual(between["price_max"], 90.0)

    def test_scan_ticker_honors_price_min(self) -> None:
        with patch.object(conversational, "get_ticker_data", return_value={"price": 45.0, "market_cap": 20e9}):
            hit = conversational.scan_ticker("LOW", date(2026, 6, 7), price_min=50.0, price_max=110.0)

        self.assertIsNone(hit)


if __name__ == "__main__":
    unittest.main()
