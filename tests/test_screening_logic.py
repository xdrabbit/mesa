"""Risk-hardening regression tests (Fix 1 earnings gate, Fix 2 solvency gate).

These cover the hardening logic only. The earnings-gate tests exclude an expiration before the
option chain is ever read, so they are independent of whether the scan pulls calls or puts.
(The baseline put-selection and conversational-parsing tests live on
`feat/csp-conversion-band` alongside the code they exercise.)
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
import sys
import unittest

sys.modules.setdefault("telegram", SimpleNamespace(Bot=object))
sys.modules.setdefault("yfinance", SimpleNamespace(Ticker=object))
sys.modules.setdefault("mesa.telegram_send", SimpleNamespace(send=lambda message: None))
sys.modules.setdefault("mesa.models", SimpleNamespace(load_positions=lambda: []))
sys.modules.setdefault("mesa.universe", SimpleNamespace(load_universe=lambda: []))

from mesa import prospector


class FakeRows:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def iterrows(self):
        for idx, row in enumerate(self.rows):
            yield idx, row


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
