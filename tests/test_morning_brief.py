"""The brief must report only what the engine found — no placeholder strikes or premiums."""
from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from mesa import morning_brief as MB
from mesa import prospector as P
from tests.test_engine import _cand

NOW = datetime(2026, 11, 30, 7, 45, tzinfo=ZoneInfo("America/Denver"))


class MorningBriefTest(unittest.TestCase):
    def test_header_shows_both_timezones(self) -> None:
        text = MB.format_brief(P.ScanResult([], [], 0, []), NOW)
        self.assertIn("Monday, November 30, 2026", text)
        self.assertIn("07:45 AM MT (ET: 09:45 AM)", text)

    def test_picks_come_from_the_engine_and_are_capped(self) -> None:
        cands = [_cand(t, 90 + i, 0.08) for i, t in enumerate(["AAA", "BBB", "CCC", "DDD"])]
        text = MB.format_brief(P.ScanResult(cands, [], 40, []), NOW)
        self.assertIn("2026-07-17", text)  # a real expiry, not a made-up "now + 45 days"
        self.assertEqual(text.count(" put ("), MB.PICKS)

    def test_main_scans_and_sends(self) -> None:
        result = P.ScanResult([_cand("KO", 92, 0.08)], [], 1, ["KO"])
        with patch.object(MB, "default_targets", return_value=([("KO", True)], ["note"])), \
                patch.object(MB, "scan", return_value=result) as scan, \
                patch.object(MB, "send") as send, patch("builtins.print"):
            MB.main()
        scan.assert_called_once_with([("KO", True)])
        self.assertIn("note", send.call_args.args[0])
        self.assertIn("KO", send.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
