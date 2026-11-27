#!/usr/bin/env python3
"""MESA Morning Brief — weekday-morning cash-secured-put ideas.

Runs the shared prospector engine over the priority watchlist plus the shortlist saved by the
last full scan, and sends the best put per ticker. Every strike, expiry and credit comes from
live option quotes, and every gate in SCREENING_STRATEGY.md applies (earnings through expiry,
solvency, delta band, liquidity).

Run during market hours (7:30-14:00 MT): outside them Yahoo returns empty option quotes and the
brief says so instead of reporting a misleading "nothing qualifies".
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from mesa.prospector import ScanResult, default_targets, format_report, scan
from mesa.telegram_send import send

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
MT = ZoneInfo("America/Denver")
PICKS = 3


def format_brief(result: ScanResult, now: datetime | None = None) -> str:
    now = (now or datetime.now(MT)).astimezone(MT)
    et = now.astimezone(ET)
    header = (
        f"🌅 *MESA Morning Brief* — {now:%A, %B %d, %Y}\n"
        f"⏰ {now:%I:%M %p} MT (ET: {et:%I:%M %p})"
    )
    return f"{header}\n\n{format_report(result, now.date(), limit=PICKS)}"


def main() -> None:
    """Scan and send the morning brief."""
    logging.basicConfig(level=logging.INFO)
    log.info("Starting morning brief scan...")

    targets, notes = default_targets(universe=False)
    result = scan(targets)
    result.notes.extend(notes)

    brief = format_brief(result)
    print(brief)
    send(brief)
    log.info("Brief sent: %d picks from %d tickers", len(result.candidates), result.scanned)


if __name__ == "__main__":
    main()
