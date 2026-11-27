"""Prospector engine: delta, live-quote handling, dedupe, reporting, target building."""
from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from mesa import prospector as P
from tests.helpers import TODAY, make_ticker, put_row

INFO = {"currentPrice": 100.0}


class PutDeltaTest(unittest.TestCase):
    def test_sane_values(self) -> None:
        atm = P._put_delta(100, 100, 40, 0.35)
        self.assertTrue(0.40 < atm < 0.50)  # slightly under 0.5 with a positive rate
        self.assertAlmostEqual(P._put_delta(100, 92, 40, 0.35), 0.207, places=2)
        self.assertLess(P._put_delta(100, 80, 40, 0.35), 0.03)

    def test_monotonic_in_strike(self) -> None:
        deltas = [P._put_delta(100, k, 40, 0.35) for k in (80, 85, 90, 95, 99)]
        self.assertEqual(deltas, sorted(deltas))

    def test_undefined_inputs(self) -> None:
        self.assertIsNone(P._put_delta(100, 90, 40, 0.0))
        self.assertIsNone(P._put_delta(100, 90, 0, 0.35))
        self.assertIsNone(P._put_delta(0, 90, 40, 0.35))


class ScoreOptionsTest(unittest.TestCase):
    def test_delta_band_is_enforced_without_chain_greeks(self) -> None:
        # 94 (~0.26 delta) is in the 0.20-0.35 band; 85 (~0.07) is not. yfinance has no greeks,
        # so this only works because delta is estimated from IV.
        t = make_ticker([put_row(94.0), put_row(85.0)])
        cands, miss = P._score_options(t, INFO, "TEST", True, TODAY)
        self.assertEqual([c.strike for c in cands], [94.0])
        self.assertIsNone(miss)
        self.assertTrue(0.20 <= cands[0].delta <= 0.35)
        self.assertEqual(cands[0].expiry, "2026-07-17")

    def test_dead_quotes_are_reported_as_closed_market_not_as_a_filter(self) -> None:
        # After hours Yahoo returns bid=ask=0 and junk IV; blame the quote, not delta/IV.
        t = make_ticker([put_row(94.0, bid=0.0, ask=0.0, iv=0.02)])
        cands, miss = P._score_options(t, INFO, "TEST", True, TODAY)
        self.assertEqual(cands, [])
        self.assertEqual(miss.reason, P.NO_QUOTE_REASON)

    def test_zero_bid_strikes_in_an_open_market_are_not_called_market_closed(self) -> None:
        # Live-validation regression: far-OTM strikes often have no bid while the market is open.
        # With one live row the real blocker (OI here) must be named, not "market closed".
        rows = [put_row(94.0, oi=10), put_row(88.0, bid=0.0, ask=0.0), put_row(87.0, bid=0.0, ask=0.0),
                put_row(86.0, bid=0.0, ask=0.0)]
        _, miss = P._score_options(make_ticker(rows), INFO, "TEST", True, TODAY)
        self.assertEqual(miss.reason, f"open interest under {P.MIN_OPEN_INTEREST}")

    def test_chain_fetch_failures_are_reported_not_swallowed(self) -> None:
        def boom(_):
            raise RuntimeError("429")

        t = make_ticker([])
        t.option_chain = boom
        cands, miss = P._score_options(t, INFO, "TEST", True, TODAY)
        self.assertEqual((cands, miss.reason), ([], P.DATA_ERROR_CHAIN))

    def test_min_iv_param(self) -> None:
        t = make_ticker([put_row(94.0, iv=0.35)])
        loose, _ = P._score_options(t, INFO, "TEST", True, TODAY, P.ScanParams(min_iv=0.30))
        strict, miss = P._score_options(t, INFO, "TEST", True, TODAY, P.ScanParams(min_iv=0.50))
        self.assertEqual(len(loose), 1)
        self.assertEqual(strict, [])
        self.assertIn("IV outside", miss.reason)

    def test_price_band_param(self) -> None:
        t = make_ticker([put_row(94.0)])
        cands, miss = P._score_options(t, INFO, "TEST", True, TODAY, P.ScanParams(price_min=120, price_max=200))
        self.assertEqual(cands, [])
        self.assertIn("outside scan range", miss.reason)

    def test_missing_price_is_a_miss_not_a_crash(self) -> None:
        cands, miss = P._score_options(make_ticker([]), {}, "TEST", True, TODAY)
        self.assertEqual((cands, miss.reason), ([], "no price data"))

    def test_dominant_blocker_is_named(self) -> None:
        rows = [put_row(94.0, oi=10), put_row(93.0, oi=20), put_row(92.0, bid=1.0, ask=3.0)]
        _, miss = P._score_options(make_ticker(rows), INFO, "TEST", True, TODAY)
        self.assertEqual(miss.reason, f"open interest under {P.MIN_OPEN_INTEREST}")


class PrefilterTest(unittest.TestCase):
    def _prefilter(self, info: dict, params: P.ScanParams = P.DEFAULT_PARAMS):
        with patch.object(P.yf, "Ticker", return_value=SimpleNamespace(info=info)):
            return P._prefilter("TEST", params)

    BASE = {"marketCap": 5e10, "currentPrice": 45.0, "country": "United States", "sector": "Technology"}

    def test_price_below_min_rejected(self) -> None:
        _, _, reason = self._prefilter(self.BASE)
        self.assertIn("outside $50-$110", reason)

    def test_custom_band_admits_it(self) -> None:
        t, info, reason = self._prefilter(self.BASE, P.ScanParams(price_min=30, price_max=80))
        self.assertIsNone(reason)
        self.assertIsNotNone(t)

    def test_info_errors_are_retried(self) -> None:
        calls = {"n": 0}

        class Flaky:
            @property
            def info(self):
                calls["n"] += 1
                if calls["n"] < 3:
                    raise RuntimeError("429")
                return {"marketCap": 5e10, "currentPrice": 80.0, "sector": "Technology"}

        with patch.object(P.yf, "Ticker", return_value=Flaky()), patch.object(P.time, "sleep"):
            t, _, reason = P._prefilter("TEST")
        self.assertIsNone(reason)
        self.assertEqual(calls["n"], 3)


class InfoStubTest(unittest.TestCase):
    """Yahoo answers ~1 in 3 parallel `.info` calls with {'trailingPegRatio': None}."""

    GOOD = {"marketCap": 5e10, "currentPrice": 80.0, "sector": "Technology"}

    def test_stub_is_retried_on_a_fresh_ticker(self) -> None:
        made = []

        def fake_ticker(symbol):
            obj = SimpleNamespace(info={"trailingPegRatio": None} if len(made) < 2 else self.GOOD)
            made.append(obj)
            return obj

        with patch.object(P.yf, "Ticker", side_effect=fake_ticker), patch.object(P.time, "sleep"):
            t, _, reason = P._prefilter("KO")
        self.assertIsNone(reason)
        self.assertEqual(len(made), 3)  # a new Ticker each attempt
        self.assertIs(t, made[-1])

    def test_persistent_stub_is_reported_as_incomplete_not_as_missing_cap(self) -> None:
        stub = SimpleNamespace(info={"trailingPegRatio": None})
        with patch.object(P.yf, "Ticker", return_value=stub), patch.object(P.time, "sleep"):
            _, _, reason = P._prefilter("KO")
        self.assertIn("incomplete data", reason)

    def test_genuinely_missing_cap_keeps_its_own_reason(self) -> None:
        odd = SimpleNamespace(info={"currentPrice": 80.0, "country": "United States"})
        with patch.object(P.yf, "Ticker", return_value=odd), patch.object(P.time, "sleep"):
            _, _, reason = P._prefilter("ODD")
        self.assertEqual(reason, "no market cap data")


def _cand(ticker: str, strike: float, cushion: float, priority: bool = False) -> P.Candidate:
    return P.Candidate(
        ticker=ticker, is_priority=priority, price=100.0, iv=0.35, strike=strike, premium=2.0,
        dte=40, annualized=0.2, cushion_pct=cushion, oi=800, spread_pct=0.03,
        premium_pct_of_strike=2.0, expiry="2026-07-17", delta=0.26,
    )


class ScanTest(unittest.TestCase):
    def _scan(self, outcomes: dict, targets, **kw):
        with patch.object(P, "_warm_up"), patch.object(P, "_scan_one", side_effect=lambda t, *a, **k: outcomes[t]):
            return P.scan(targets, today=TODAY, **kw)

    def test_one_pick_per_ticker_best_first(self) -> None:
        # Old behaviour: five strikes of one ticker could fill the whole TOP PICKS list.
        outcomes = {
            "AAA": P._Outcome([_cand("AAA", 94, 0.06), _cand("AAA", 90, 0.10), _cand("AAA", 92, 0.08)], None, True),
            "BBB": P._Outcome([_cand("BBB", 94, 0.06)], None, True),
        }
        res = self._scan(outcomes, [("AAA", False), ("BBB", False)])
        self.assertEqual(sorted(c.ticker for c in res.candidates), ["AAA", "BBB"])
        self.assertEqual(res.scanned, 2)

    def test_priority_floats_first(self) -> None:
        outcomes = {
            "AAA": P._Outcome([_cand("AAA", 90, 0.10)], None, True),
            "PRI": P._Outcome([_cand("PRI", 95, 0.05, priority=True)], None, True),
        }
        res = self._scan(outcomes, [("AAA", False), ("PRI", True)])
        self.assertEqual(res.candidates[0].ticker, "PRI")

    def test_prefiltered_tickers_feed_the_shortlist(self) -> None:
        outcomes = {
            "AAA": P._Outcome([], P.NearMiss("AAA", False, "x"), True),
            "BBB": P._Outcome([], None, False),
        }
        res = self._scan(outcomes, [("AAA", False), ("BBB", False)])
        self.assertEqual(res.prefiltered, ["AAA"])

    def test_worker_exception_does_not_kill_the_scan(self) -> None:
        def boom(t, *a, **k):
            if t == "BAD":
                raise RuntimeError("yahoo fell over")
            return P._Outcome([_cand(t, 94, 0.06)], None, True)

        with patch.object(P, "_warm_up"), patch.object(P, "_scan_one", side_effect=boom):
            res = P.scan([("BAD", False), ("OK", False)], today=TODAY)
        self.assertEqual([c.ticker for c in res.candidates], ["OK"])

    def test_closed_market_adds_a_note(self) -> None:
        miss = lambda t: P._Outcome([], P.NearMiss(t, False, P.NO_QUOTE_REASON), True)  # noqa: E731
        res = self._scan({"A": miss("A"), "B": miss("B")}, [("A", False), ("B", False)])
        self.assertTrue(any("market is probably closed" in n for n in res.notes))

    def test_throttled_scan_says_so_instead_of_returning_quietly_fewer_picks(self) -> None:
        # Live validation: a rate-limited run dropped ~30 of 517 tickers and still said "517 scanned".
        bad = P._Outcome([], None, False, data_error=True)
        outcomes = {f"T{i}": bad for i in range(10)} | {"OK": P._Outcome([_cand("OK", 94, 0.06)], None, True)}
        res = self._scan(outcomes, [(t, False) for t in outcomes])
        self.assertEqual(len(res.data_errors), 10)
        self.assertTrue(any("10 of 11 tickers" in n for n in res.notes))

    def test_a_single_flaky_ticker_is_not_worth_a_warning(self) -> None:
        outcomes = {f"T{i}": P._Outcome([_cand(f"T{i}", 94, 0.06)], None, True) for i in range(100)}
        outcomes["BAD"] = P._Outcome([], None, False, data_error=True)
        res = self._scan(outcomes, [(t, False) for t in outcomes])
        self.assertEqual(res.data_errors, ["BAD"])
        self.assertEqual(res.notes, [])

    def test_scan_one_flags_yahoo_failures_but_not_screening_rejections(self) -> None:
        with patch.object(P, "_prefilter", return_value=(None, None, "yfinance returned incomplete data (rate-limited?)")):
            self.assertTrue(P._scan_one("A", False, TODAY).data_error)
        with patch.object(P, "_prefilter", return_value=(None, None, "price $300.00 outside $50-$110")):
            self.assertFalse(P._scan_one("A", False, TODAY).data_error)

    def test_no_closed_note_when_real_picks_exist(self) -> None:
        # A pick needs a live bid/ask, so the market is open — even if most tickers had dead rows.
        dead = lambda t: P._Outcome([], P.NearMiss(t, False, P.NO_QUOTE_REASON), True)  # noqa: E731
        outcomes = {"A": dead("A"), "B": dead("B"), "C": dead("C"),
                    "OK": P._Outcome([_cand("OK", 94, 0.06)], None, True)}
        res = self._scan(outcomes, [(t, False) for t in outcomes])
        self.assertEqual([c.ticker for c in res.candidates], ["OK"])
        self.assertEqual(res.notes, [])


class ReportTest(unittest.TestCase):
    def test_pick_line_has_everything_needed_to_place_the_trade(self) -> None:
        line = _cand("KO", 92.5, 0.075, priority=True).format_line(1)
        for needle in ("⭐ KO", "2026-07-17", "$92.5 put", "40d", "Δ0.26", "$2.00 credit", "cushion 8%", "OI 800"):
            self.assertIn(needle, line)

    def test_report_with_picks(self) -> None:
        res = P.ScanResult([_cand("KO", 92, 0.08)], [], scanned=40, prefiltered=["KO"])
        text = P.format_report(res, TODAY)
        self.assertIn("TOP PICKS", text)
        self.assertIn("(40 scanned)", text)

    def test_report_without_picks_lists_misses_and_notes(self) -> None:
        res = P.ScanResult([], [P.NearMiss("KO", True, "no live bid/ask")], 1, [], notes=["heads up"])
        text = P.format_report(res, TODAY)
        self.assertIn("No qualifying picks", text)
        self.assertIn("KO — no live bid/ask", text)
        self.assertIn("heads up", text)

    def test_explain_lists_misses_alongside_picks(self) -> None:
        res = P.ScanResult([_cand("KO", 92, 0.08)], [P.NearMiss("WMT", True, "price $132 outside")], 2, ["KO"])
        self.assertNotIn("WMT", P.format_report(res, TODAY))
        text = P.format_report(res, TODAY, explain=True)
        self.assertIn("Not qualifying", text)
        self.assertIn("WMT — price $132 outside", text)


class BuildTargetsTest(unittest.TestCase):
    def test_open_positions_are_excluded_and_priority_is_flagged(self) -> None:
        open_pos = [SimpleNamespace(ticker="KO", closed=False), SimpleNamespace(ticker="PG", closed=True)]
        with patch.object(P, "load_positions", return_value=open_pos), \
                patch.object(P, "load_universe", return_value=["KO", "PG", "ZZZ", "AAPL"]):
            targets, notes = P.build_targets(universe=True)
        names = dict(targets)
        self.assertNotIn("KO", names)
        self.assertTrue(names["AAPL"])  # on the priority watchlist
        self.assertFalse(names["ZZZ"])
        self.assertIn("PG", names)  # closed positions don't block a ticker
        self.assertEqual(notes, [])

    def test_universe_failure_degrades_to_watchlist_instead_of_crashing(self) -> None:
        with patch.object(P, "load_positions", return_value=[]), \
                patch.object(P, "load_universe", side_effect=RuntimeError("wikipedia moved")):
            targets, notes = P.build_targets(universe=True)
        self.assertEqual(len(targets), len(P.WATCHLIST))
        self.assertTrue(any("universe unavailable" in n.lower() for n in notes))


if __name__ == "__main__":
    unittest.main()
