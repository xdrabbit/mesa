"""Universe loading must never take the cron job down (Wikipedia dropped its Nasdaq-100 table)."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from mesa import universe as U


class FetchTest(unittest.TestCase):
    def test_ndx_failure_still_yields_sp500(self) -> None:
        with patch.object(U, "_fetch_sp500", return_value=["AAPL", "KO"]), \
                patch.object(U, "_fetch_ndx", side_effect=RuntimeError("table not found")):
            self.assertEqual(U.fetch_universe(), ["AAPL", "KO"])

    def test_union_when_both_work(self) -> None:
        with patch.object(U, "_fetch_sp500", return_value=["AAPL", "KO"]), \
                patch.object(U, "_fetch_ndx", return_value=["AAPL", "NVDA"]):
            self.assertEqual(U.fetch_universe(), ["AAPL", "KO", "NVDA"])

    def test_raises_only_if_everything_fails(self) -> None:
        with patch.object(U, "_fetch_sp500", side_effect=RuntimeError("a")), \
                patch.object(U, "_fetch_ndx", side_effect=RuntimeError("b")):
            with self.assertRaises(RuntimeError):
                U.fetch_universe()

    def test_ndx_falls_back_to_wikipedia_when_api_fails(self) -> None:
        with patch.object(U, "_fetch_ndx_api", side_effect=RuntimeError("api down")), \
                patch.object(U, "_fetch_ndx_wikipedia", return_value=["NVDA"]):
            self.assertEqual(U._fetch_ndx(), ["NVDA"])


class CacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        patchers = [
            patch.object(U, "CACHE_PATH", root / "universe.json"),
            patch.object(U, "SHORTLIST_PATH", root / "shortlist.json"),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def write_universe(self, age: timedelta, tickers: list[str]) -> None:
        stamp = (datetime.now(timezone.utc) - age).isoformat()
        U.CACHE_PATH.write_text(json.dumps({"fetched_at": stamp, "tickers": tickers}))

    def test_fresh_cache_is_used_without_fetching(self) -> None:
        self.write_universe(timedelta(days=1), ["KO"])
        with patch.object(U, "fetch_universe", side_effect=AssertionError("should not fetch")):
            self.assertEqual(U.load_universe(), ["KO"])

    def test_stale_cache_beats_crashing(self) -> None:
        self.write_universe(timedelta(days=30), ["KO", "PG"])
        with patch.object(U, "fetch_universe", side_effect=RuntimeError("sources down")):
            self.assertEqual(U.load_universe(), ["KO", "PG"])

    def test_no_cache_and_no_sources_raises(self) -> None:
        with patch.object(U, "fetch_universe", side_effect=RuntimeError("sources down")):
            with self.assertRaises(RuntimeError):
                U.load_universe()

    def test_refresh_writes_the_cache(self) -> None:
        with patch.object(U, "fetch_universe", return_value=["A", "B"]):
            self.assertEqual(U.load_universe(), ["A", "B"])
        self.assertEqual(json.loads(U.CACHE_PATH.read_text())["tickers"], ["A", "B"])


class ShortlistTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(U, "SHORTLIST_PATH", Path(self.tmp.name) / "shortlist.json")
        p.start()
        self.addCleanup(p.stop)

    def test_roundtrip_dedupes_and_sorts(self) -> None:
        U.save_shortlist(["PG", "KO", "KO"])
        self.assertEqual(U.load_shortlist(), ["KO", "PG"])

    def test_missing_expired_or_corrupt_is_empty(self) -> None:
        self.assertEqual(U.load_shortlist(), [])
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        U.SHORTLIST_PATH.write_text(json.dumps({"saved_at": old, "tickers": ["KO"]}))
        self.assertEqual(U.load_shortlist(), [])
        U.SHORTLIST_PATH.write_text("{not json")
        self.assertEqual(U.load_shortlist(), [])


if __name__ == "__main__":
    unittest.main()
