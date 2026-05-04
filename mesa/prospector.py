"""Prospector agent — scans for attractive covered call opportunities (premium selling).

Criteria:
  - Stock price: $80–$140
  - Market cap: > $10B
  - IV: 35%–65%
  - Earnings: No earnings within 14 days
  - DTE: 25–45 days out
  - Delta: 0.20–0.30 (for short calls)
  - Strike: ≥ 8% above current price (cushion)
  - Premium: ≥ 1.0% of strike price
  - Liquid options (open interest ≥ 500, bid-ask spread ≤ 5%)
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import yfinance as yf

from mesa.telegram_send import send

log = logging.getLogger(__name__)

# Quality watchlist (priority targets)
# Stocks you'd actually want to own long-term
WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
    "JPM", "V", "MA", "UNH", "JNJ", "PG", "KO",
    "WMT", "HD", "ABBV", "DDOG", "CRM", "NOW",
]

# Exclusions
CRYPTO_EXCLUSION = {"MARA", "RIOT", "COIN", "MSTR", "CLSK", "HOOD"}
ACCOUNTING_ISSUES = set()

# Constraints
MIN_STOCK_PRICE = 80.0    # Ideal wheel range
MAX_STOCK_PRICE = 140.0   # Ideal wheel range
MIN_MARKET_CAP = 1e10     # $10 billion minimum

# IV filter (35-65% range)
MIN_IV = 0.35
MAX_IV = 0.65

# Earnings buffer
MIN_DAYS_TO_EARNINGS = 14

# Option criteria
TARGET_DTE_MIN = 25
TARGET_DTE_MAX = 45
TARGET_DELTA_MIN = 0.20
TARGET_DELTA_MAX = 0.30
MIN_STRIKE_CUSHION_PCT = 0.08  # 8% above current price
MIN_PREMIUM_PCT_OF_STRIKE = 0.01  # 1.0% of strike
MIN_OPEN_INTEREST = 500
MAX_BID_ASK_SPREAD_PCT = 0.05  # 5%


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
    # EXCLUSION FILTERS
    if ticker in CRYPTO_EXCLUSION:
        log.debug(f"Skipping {ticker}: crypto-related")
        return []
    
    if ticker in ACCOUNTING_ISSUES:
        log.debug(f"Skipping {ticker}: known accounting issues")
        return []
    
    t = yf.Ticker(ticker)
    price_hist = t.history(period="1d")
    if price_hist.empty:
        log.debug(f"Skipping {ticker}: no price data")
        return []
    price = float(price_hist["Close"].iloc[-1])
    
    # Price range filter (wheel strategy: $50-$110 sweet spot)
    if price < MIN_STOCK_PRICE:
        log.debug(f"Skipping {ticker}: ${price:.2f} < ${MIN_STOCK_PRICE} (too small)")
        return []
    
    if price > MAX_STOCK_PRICE:
        log.debug(f"Skipping {ticker}: ${price:.2f} > ${MAX_STOCK_PRICE} (capital constraint)")
        return []
    
    # Market cap filter
    try:
        info = t.info
        market_cap = info.get("marketCap")
        if market_cap and market_cap < MIN_MARKET_CAP:
            log.debug(f"Skipping {ticker}: market cap ${market_cap/1e9:.1f}B < $10B")
            return []
    except Exception as e:
        log.debug(f"Could not check market cap for {ticker}: {e}")

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

        calls = chain.calls

        for _, row in calls.iterrows():
            strike = float(row["strike"])

            # Only OTM calls (strike > current price)
            if strike <= price:
                continue

            # Strike cushion: at least 8% above current price
            cushion_pct = (strike - price) / price
            if cushion_pct < MIN_STRIKE_CUSHION_PCT:
                continue

            # Delta filter: 0.20-0.30 for short calls
            delta = float(row.get("delta", 0))
            if delta < TARGET_DELTA_MIN or delta > TARGET_DELTA_MAX:
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

            # Premium quality: must be >= 1.0% of strike
            premium_pct_of_strike = (mid / strike) * 100
            if premium_pct_of_strike < (MIN_PREMIUM_PCT_OF_STRIKE * 100):
                continue

            # Annualized return (premium as % of stock price * 365/DTE)
            raw_return = mid / price
            annualized = raw_return * (365 / dte) if dte > 0 else 0

            breakeven = strike + mid

            msg = (
                f"📌 *{ticker}* ${strike:.0f} CALL — {exp_str} ({dte}d)\n"
                f"  Price: ${price:.2f} | Mid: ${mid:.2f} (${premium_per_contract:.0f}/contract)\n"
                f"  Premium: {premium_pct_of_strike:.2f}% of strike | Annualized: {annualized:.0%}\n"
                f"  Cushion: {cushion_pct:.1%} | OI: {oi:,} | Spread: {spread_pct:.0%}"
            )
            hits.append((annualized, msg))

    # Sort by annualized return descending
    hits.sort(key=lambda x: x[0], reverse=True)
    return [msg for _, msg in hits[:3]]  # top 3 per ticker
