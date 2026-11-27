#!/usr/bin/env python3
"""Verify the prospector risk filters (Fix 1 earnings, Fix 2 solvency, Fix 3 ranking).

Given a set of test tickers with MOCKED fundamentals/earnings, prints whether each is
ADMITTED or EXCLUDED and why. INTC (earnings straddler) and SATS (distressed credit) are
included as required test cases.

This script uses MOCKED inputs only — it places no trades and makes NO network/market-data
calls. yfinance, telegram, and the live position/universe loaders are stubbed before import,
so it runs in any environment. (A live mode would require yfinance; it is intentionally not
enabled here to keep the check deterministic and offline.)

Run:  python3 scripts/verify_risk_filters.py
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from types import SimpleNamespace

# --- Make the repo importable and stub heavy/live deps BEFORE importing mesa modules. ---
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

sys.modules.setdefault("yfinance", SimpleNamespace(Ticker=object))
sys.modules.setdefault("telegram", SimpleNamespace(Bot=object))
sys.modules.setdefault("mesa.telegram_send", SimpleNamespace(send=lambda message: None))
sys.modules.setdefault("mesa.models", SimpleNamespace(load_positions=lambda: []))
sys.modules.setdefault("mesa.universe", SimpleNamespace(load_universe=lambda: [], save_shortlist=lambda t: None, load_shortlist=lambda: []))

from mesa import prospector  # noqa: E402
from mesa.scoring import risk_score  # noqa: E402

# Representative option life used for the earnings comparison (mid of the 30-60 DTE band).
DTE_FOR_TEST = 45


class _FakeTicker:
    """Minimal stand-in exposing only the `.calendar` attribute `_next_earnings_date` reads."""

    def __init__(self, earnings_offset_days):
        if earnings_offset_days is None:
            self.calendar = None  # ⇒ unknown ⇒ fail closed
        else:
            ed = date.today() + timedelta(days=earnings_offset_days)
            self.calendar = {"Earnings Date": [ed]}


# (ticker, info, earnings_offset_days_from_today, human note)
TEST_CASES = [
    ("INTC", {"profitMargins": 0.04}, 20, "earnings straddler (earnings inside 45-DTE option)"),
    ("SATS", {"profitMargins": -0.40}, 120, "distressed credit (manual solvency list)"),
    ("MSFT", {"profitMargins": 0.34}, 120, "healthy mega-cap, earnings outside option life"),
    ("NODATA", {"profitMargins": 0.10}, None, "no earnings data available (fail closed)"),
    ("REDINK", {"profitMargins": -0.30}, 120, "deeply negative trailing profit margin"),
    ("THINPM", {}, 120, "no profitMargins field (solvency fails OPEN, earnings clear)"),
]


def evaluate(ticker: str, info: dict, earnings_offset_days, today: date, exp_date: date):
    """Return (admitted, reason) applying the same gates the scan applies, in scan order."""
    # Fix 2 — solvency gate runs first (prefilter stage in the real pipeline).
    solv_ok, solv_reason = prospector._solvency_admits(info, ticker)
    if not solv_ok:
        return False, solv_reason

    # Fix 1 — per-expiration earnings gate (fail closed on unknown).
    earnings_date = prospector._next_earnings_date(_FakeTicker(earnings_offset_days), today)
    earn_ok, earn_reason = prospector._earnings_admits(earnings_date, exp_date)
    if not earn_ok:
        return False, earn_reason

    return True, "admitted (solvency clear + earnings outside option life)"


def print_admission_table() -> int:
    today = date.today()
    exp_date = today + timedelta(days=DTE_FOR_TEST)

    print("=" * 78)
    print("PROSPECTOR RISK-FILTER VERIFICATION  (mocked inputs, no live data)")
    print(f"today={today}  test expiration={exp_date}  (~{DTE_FOR_TEST} DTE)")
    print("=" * 78)

    excluded = 0
    for ticker, info, off, note in TEST_CASES:
        admitted, reason = evaluate(ticker, info, off, today, exp_date)
        verdict = "ADMITTED" if admitted else "EXCLUDED"
        if not admitted:
            excluded += 1
        print(f"  {verdict:8}  {ticker:8}  {reason}")
        print(f"            {'':8}  └─ {note}")

    print("-" * 78)
    print(f"  {len(TEST_CASES)} tickers — {excluded} excluded, {len(TEST_CASES) - excluded} admitted")

    # Required-case assertions: INTC and SATS must both be excluded.
    intc = evaluate("INTC", {"profitMargins": 0.04}, 20, today, exp_date)[0]
    sats = evaluate("SATS", {"profitMargins": -0.40}, 120, today, exp_date)[0]
    ok = (not intc) and (not sats)
    print(f"  REQUIRED: INTC excluded={not intc}  SATS excluded={not sats}  -> {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def print_ranking_demo() -> None:
    """Fix 3: show the risk-adjusted blend no longer rewards raw fear (high IV / fat premium)."""
    print()
    print("=" * 78)
    print("FIX 3 — RISK-ADJUSTED RANKING DEMO (mesa.scoring.risk_score)")
    print("=" * 78)
    # premium_pct_of_strike is in PERCENT. Same liquidity for all three.
    rows = [
        ("moderate-IV, solid cushion", dict(premium_pct_of_strike=1.5, cushion_pct=0.12, iv=0.45, oi=600, spread_pct=0.05)),
        ("HIGH-IV fat premium, thin cushion", dict(premium_pct_of_strike=3.0, cushion_pct=0.05, iv=1.20, oi=600, spread_pct=0.05)),
        ("low-IV, low premium, big cushion", dict(premium_pct_of_strike=0.8, cushion_pct=0.18, iv=0.30, oi=600, spread_pct=0.05)),
    ]
    scored = sorted(((label, risk_score(**kw)) for label, kw in rows), key=lambda r: -r[1])
    for i, (label, score) in enumerate(scored, 1):
        print(f"  #{i}  score {score:5.1f}/100  {label}")
    print("  (Under the OLD raw-annualized sort, the HIGH-IV fat-premium row would have ranked #1.)")


if __name__ == "__main__":
    rc = print_admission_table()
    print_ranking_demo()
    sys.exit(rc)
