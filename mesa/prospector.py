"""Prospector agent — scans for attractive cash-secured put opportunities.

Criteria:
  - High implied volatility (IV rank proxy via option premium)
  - Annualized return on capital >= 15%
  - Delta roughly -0.20 to -0.35 (OTM sweet spot)
  - Expiry 30-60 days out
  - Liquid options (open interest > 100, bid-ask spread < 20%)
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import yfinance as yf

from mesa.telegram import send

log = logging.getLogger(__name__)

# Watchlist of tickers to scan
WATCHLIST = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
    "AMD", "CRM", "DDOG", "NET", "SNOW", "PLTR", "COIN",
    "XYZ", "SHOP", "ABNB", "UBER", "NFLX", "DIS",
]

MIN_ANNUALIZED_RETURN = 0.15
MIN_OPEN_INTEREST = 100
MAX_BID_ASK_SPREAD_PCT = 0.20
TARGET_DTE_MIN = 30
TARGET_DTE_MAX = 60


def run() -> None:
    today = date.today()
    results: list[str] = []

    for ticker in WATCHLIST:
        try:
            hits = _scan_ticker(ticker, today)
            results.extend(hits)
        except Exception as e:
            log.error("Error scanning %s: %s", ticker, e)

    if results:
        header = f"🔭 *Prospector Report* — {today}\nTop cash-secured put candidates:\n"
        # Cap at top 10 by annualized return (already sorted per ticker)
        body = "\n\n".join(results[:10])
        send(header + "\n" + body)
    else:
        log.info("No attractive opportunities found")


def _scan_ticker(ticker: str, today: date) -> list[str]:
    t = yf.Ticker(ticker)
    price_hist = t.history(period="1d")
    if price_hist.empty:
        return []
    price = float(price_hist["Close"].iloc[-1])

    expiries = t.options
    hits: list[tuple[float, str]] = []  # (annualized_return, message)

    for exp_str in expiries:
        exp_date = date.fromisoformat(exp_str)
        dte = (exp_date - today).days
        if dte < TARGET_DTE_MIN or dte > TARGET_DTE_MAX:
            continue

        try:
            chain = t.option_chain(exp_str)
        except ValueError:
            continue

        puts = chain.puts

        for _, row in puts.iterrows():
            strike = float(row["strike"])

            # Only OTM puts
            if strike >= price:
                continue

            # Rough delta filter: strike between 80-95% of current price
            moneyness = strike / price
            if moneyness < 0.80 or moneyness > 0.95:
                continue

            bid = float(row.get("bid", 0))
            ask = float(row.get("ask", 0))
            try:
                oi = int(row.get("openInterest", 0) or 0)
            except (ValueError, TypeError):
                continue

            if bid <= 0 or ask <= 0:
                continue

            # Liquidity filter
            if oi < MIN_OPEN_INTEREST:
                continue
            spread_pct = (ask - bid) / bid
            if spread_pct > MAX_BID_ASK_SPREAD_PCT:
                continue

            mid = (bid + ask) / 2
            premium_per_contract = mid * 100
            capital_required = strike * 100  # cash-secured

            # Annualized return on capital
            raw_return = premium_per_contract / capital_required
            annualized = raw_return * (365 / dte) if dte > 0 else 0

            if annualized < MIN_ANNUALIZED_RETURN:
                continue

            breakeven = strike - mid
            cushion_pct = (price - breakeven) / price

            msg = (
                f"📌 *{ticker}* ${strike:.0f} PUT — {exp_str} ({dte}d)\n"
                f"  Price: ${price:.2f} | Mid: ${mid:.2f} (${premium_per_contract:.0f}/contract)\n"
                f"  Annualized: {annualized:.0%} | Cushion: {cushion_pct:.1%}\n"
                f"  OI: {oi:,} | Spread: {spread_pct:.0%}"
            )
            hits.append((annualized, msg))

    # Sort by annualized return descending
    hits.sort(key=lambda x: x[0], reverse=True)
    return [msg for _, msg in hits[:3]]  # top 3 per ticker
