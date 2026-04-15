from __future__ import annotations

import yfinance as yf


def get_price(ticker: str) -> float | None:
    """Get the latest price for a ticker."""
    t = yf.Ticker(ticker)
    hist = t.history(period="1d")
    if hist.empty:
        return None
    return float(hist["Close"].iloc[-1])


def get_option_chain(ticker: str, expiry: str) -> dict | None:
    """Get the option chain for a ticker at a given expiry.

    Returns dict with 'calls' and 'puts' DataFrames, or None.
    """
    t = yf.Ticker(ticker)
    try:
        chain = t.option_chain(expiry)
    except ValueError:
        return None
    return {"calls": chain.calls, "puts": chain.puts}


def get_available_expiries(ticker: str) -> list[str]:
    """Return available option expiry dates for a ticker."""
    t = yf.Ticker(ticker)
    return list(t.options)
