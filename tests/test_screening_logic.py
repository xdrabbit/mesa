"""Screening-logic regression tests.

Covers the put-selection scan, the conversational price parsing (CSP/band work) and the
risk-hardening gates (earnings fail-closed, solvency). Everything is offline: option chains
and tickers are faked, nothing touches the network.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
import unittest

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
            # Earnings AFTER the expiration, so the Fix 1 earnings gate admits the expiration.
            calendar={"Earnings Date": [date(2026, 8, 20)]},
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


class ProspectorEarningsGateTest(unittest.TestCase):
    def test_earnings_inside_option_life_excluded(self) -> None:
        today = date(2026, 6, 7)
        chain = SimpleNamespace(
            calls=FakeRows([]),
            puts=FakeRows([]),
        )
        ticker = SimpleNamespace(
            options=["2026-07-17"],
            option_chain=lambda _: chain,
            # Earnings BEFORE the 2026-07-17 expiration ⇒ Fix 1 excludes (earnings straddler).
            calendar={"Earnings Date": [date(2026, 7, 1)]},
        )
        info = {"currentPrice": 100.0}

        candidates, miss = prospector._score_options(ticker, info, "TEST", True, today)

        self.assertEqual(candidates, [])
        self.assertIsNotNone(miss)
        self.assertIn("earnings", miss.reason)
        self.assertNotEqual(miss.reason, "earnings-unknown-excluded")

    def test_earnings_unknown_fails_closed(self) -> None:
        today = date(2026, 6, 7)
        chain = SimpleNamespace(
            calls=FakeRows([]),
            puts=FakeRows([]),
        )
        ticker = SimpleNamespace(
            options=["2026-07-17"],
            option_chain=lambda _: chain,
            calendar=None,  # No earnings data ⇒ FAIL CLOSED (exclude).
        )
        info = {"currentPrice": 100.0}

        candidates, miss = prospector._score_options(ticker, info, "TEST", True, today)

        self.assertEqual(candidates, [])
        self.assertIsNotNone(miss)
        self.assertEqual(miss.reason, "earnings-unknown-excluded")


class ProspectorSolvencyGateTest(unittest.TestCase):
    def test_distressed_name_excluded(self) -> None:
        ok, reason = prospector._solvency_admits({"profitMargins": 0.3}, "SATS")
        self.assertFalse(ok)
        self.assertIn("solvency-excluded", reason)

    def test_deeply_negative_margin_excluded(self) -> None:
        ok, reason = prospector._solvency_admits({"profitMargins": -0.35}, "REDINK")
        self.assertFalse(ok)
        self.assertIn("solvency-excluded", reason)

    def test_healthy_name_admitted(self) -> None:
        ok, reason = prospector._solvency_admits({"profitMargins": 0.25}, "MSFT")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_missing_margin_fails_open(self) -> None:
        ok, _ = prospector._solvency_admits({}, "NODATA")
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
