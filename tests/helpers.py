"""Shared fakes for offline tests: option chains and tickers without touching Yahoo."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace


class FakeRows:
    """Stands in for a DataFrame: the engine only calls .iterrows()."""

    def __init__(self, rows: list[dict]):
        self.rows = rows

    def iterrows(self):
        for idx, row in enumerate(self.rows):
            yield idx, row


def put_row(strike: float, bid: float = 1.9, ask: float = 2.0, oi: int = 500, iv: float = 0.35, **extra) -> dict:
    return {"strike": strike, "bid": bid, "ask": ask, "openInterest": oi, "impliedVolatility": iv, **extra}


def make_ticker(rows: list[dict], expiry: str = "2026-07-17", earnings: date | None = date(2026, 9, 1)):
    """A fake yfinance.Ticker with one expiry. `earnings=None` ⇒ no calendar (fails closed)."""
    chain = SimpleNamespace(calls=FakeRows([]), puts=FakeRows(rows))
    calendar = {"Earnings Date": [earnings]} if earnings else None
    return SimpleNamespace(options=[expiry], option_chain=lambda _: chain, calendar=calendar)


TODAY = date(2026, 6, 7)  # 40 DTE to the default 2026-07-17 expiry
