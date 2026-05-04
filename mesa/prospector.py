"""Prospector agent — scans for attractive covered call opportunities (premium selling).

Two-tier strategy:

TIER 1 (CORE TECH): Familiar stocks (AAPL, MSFT, NVDA, GOOGL, META, AMZN, etc.)
  - Stock price: $70-$140
  - Minimum credit: $200 per contract
  - Strike cushion: ≥ 8% above current price
  - All standard filters (IV, DTE, delta, liquidity)

TIER 2 (NON-CORE SWEET DEALS): Healthcare, airlines, energy, defense (UNH, JNJ, etc.)
  - Stock price: $70-$140
  - Minimum credit: $500 per contract (stricter)
  - Strike cushion: ≥ 10% above current price (stricter)
  - Only show if numbers are exceptional

Common filters:
  - Market cap: > $10B
  - IV: 35%–65%
  - Earnings: No earnings within 14 days
  - DTE: 25–45 days out
  - Delta: 0.20–0.30 (for short calls)
  - Premium: ≥ 1.0% of strike price
  - Liquidity: open interest ≥ 500, bid-ask spread ≤ 5%
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
MIN_STOCK_PRICE = 70.0    # Ideal wheel range
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
MIN_PREMIUM_PCT_OF_STRIKE = 0.01  # 1.0% of strike
MIN_OPEN_INTEREST = 500
MAX_BID_ASK_SPREAD_PCT = 0.05  # 5%

# Two-tier premium/cushion filters
CORE_INDUSTRIES = {
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN",
    "DDOG", "CRM", "NOW", "NET", "SNOW",
}

NON_CORE_INDUSTRIES = {
    # Healthcare
    "UNH", "JNJ", "PG", "ABBV",
    # Airlines
    "DAL", "UAL", "AAL",
    # Energy
    "XOM", "CVX", "MPC",
    # Defense
    "RTX", "BA", "NOC",
    # Finance & other
    "V", "MA", "JPM", "WMT", "HD", "KO",
}

# Tier 1 (Core tech): relaxed filters
CORE_MIN_CREDIT_PRICE = 2.00   # $200 per contract
CORE_MIN_STRIKE_CUSHION_PCT = 0.08  # 8%

# Tier 2 (Non-core sweet deals): stricter filters
NON_CORE_MIN_CREDIT_PRICE = 5.00   # $500 per contract
NON_CORE_MIN_STRIKE_CUSHION_PCT = 0.10  # 10%


def run() -> None:
    today = date.today()
    results: list[str] = []
    near_misses_core: list[tuple[float, str]] = []  # (score, message)
    near_misses_non_core: list[tuple[float, str]] = []

    for ticker in WATCHLIST:
        try:
            hits, near_miss = _scan_ticker(ticker, today)
            results.extend(hits)
            if near_miss:
                tier, score, msg = near_miss
                if tier == "CORE":
                    near_misses_core.append((score, msg))
                else:
                    near_misses_non_core.append((score, msg))
        except Exception as e:
            log.error("Error scanning %s: %s", ticker, e)

    if results:
        header = f"🔭 *Prospector Report* — {today}\nTop covered call candidates (GREEN/YELLOW only):\n"
        # Cap at top 10 by score
        body = "\n\n".join(results[:10])
        send(header + "\n" + body)
    else:
        # No opportunities found - show nearest miss in each tier
        log.info("No attractive opportunities found")
        
        message_parts = [f"🔭 *Prospector Report* — {today}\n"]
        message_parts.append("*No opportunities today.*\n")
        message_parts.append("Nearest misses:\n")
        
        # Best near-miss in CORE tier
        if near_misses_core:
            near_misses_core.sort(key=lambda x: x[0], reverse=True)
            message_parts.append("\n*CORE TECH (Nearest Miss):*\n")
            message_parts.append(near_misses_core[0][1])
        
        # Best near-miss in NON-CORE tier
        if near_misses_non_core:
            near_misses_non_core.sort(key=lambda x: x[0], reverse=True)
            message_parts.append("\n*NON-CORE (Nearest Miss):*\n")
            message_parts.append(near_misses_non_core[0][1])
        
        if near_misses_core or near_misses_non_core:
            send("\n".join(message_parts))
        else:
            log.info("No near-misses either (all stocks filtered at early stage)")


def _scan_ticker(ticker: str, today: date) -> tuple[list[str], tuple[str, float, str] | None]:
    """Scan ticker for opportunities. Returns (hits, near_miss).
    
    near_miss format: (tier, score, message) or None if no near-miss to report
    """
    # EXCLUSION FILTERS
    if ticker in CRYPTO_EXCLUSION:
        log.debug(f"Skipping {ticker}: crypto-related")
        return [], None
    
    if ticker in ACCOUNTING_ISSUES:
        log.debug(f"Skipping {ticker}: known accounting issues")
        return [], None
    
    t = yf.Ticker(ticker)
    price_hist = t.history(period="1d")
    if price_hist.empty:
        log.debug(f"Skipping {ticker}: no price data")
        return [], None
    price = float(price_hist["Close"].iloc[-1])
    
    # Price range filter (wheel strategy: $70-$140 range)
    # Track near-miss if price is close but outside range
    price_near_miss = None
    if price < MIN_STOCK_PRICE:
        log.debug(f"Skipping {ticker}: ${price:.2f} < ${MIN_STOCK_PRICE} (too small)")
        price_near_miss = (0.5, "CORE" if ticker in CORE_INDUSTRIES else "NON_CORE", 
                          f"⚠️ {ticker} ${price:.2f} — price below range (need ${MIN_STOCK_PRICE})")
        return [], price_near_miss
    
    if price > MAX_STOCK_PRICE:
        log.debug(f"Skipping {ticker}: ${price:.2f} > ${MAX_STOCK_PRICE} (capital constraint)")
        # Track near-miss for price-out-of-range
        pct_drop_needed = ((price - MAX_STOCK_PRICE) / price) * 100  # % drop needed
        price_near_miss = (0.7, "CORE" if ticker in CORE_INDUSTRIES else "NON_CORE",
                          f"⚠️ *{ticker}* ${price:.2f} — above range\n  Need {pct_drop_needed:.0f}% drop to ${MAX_STOCK_PRICE}")
        return [], price_near_miss
    
    # Market cap filter
    try:
        info = t.info
        market_cap = info.get("marketCap")
        if market_cap and market_cap < MIN_MARKET_CAP:
            log.debug(f"Skipping {ticker}: market cap ${market_cap/1e9:.1f}B < $10B")
            return [], None
    except Exception as e:
        log.debug(f"Could not check market cap for {ticker}: {e}")

    expiries = t.options
    hits: list[tuple[float, str]] = []  # (score, message)
    near_miss_candidate: tuple[float, str, str] | None = None  # (score, tier, message)

    for exp_str in expiries:
        exp_date = date.fromisoformat(exp_str)
        dte = (exp_date - today).days
        if dte < TARGET_DTE_MIN or dte > TARGET_DTE_MAX:
            continue
        
        # Reset near-miss per expiry (keep best across all expirations)
        expiry_near_miss: tuple[float, str, str] | None = None

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

            # Determine tier
            tier = "CORE" if ticker in CORE_INDUSTRIES else "NON_CORE"
            min_cushion = CORE_MIN_STRIKE_CUSHION_PCT if tier == "CORE" else NON_CORE_MIN_STRIKE_CUSHION_PCT
            min_credit = CORE_MIN_CREDIT_PRICE if tier == "CORE" else NON_CORE_MIN_CREDIT_PRICE

            # Strike cushion filter
            cushion_pct = (strike - price) / price
            if cushion_pct < min_cushion:
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
                # Track as near-miss: has good credit but low OI
                mid = (bid + ask) / 2
                premium_pct_of_strike = (mid / strike) * 100
                if mid >= min_credit and premium_pct_of_strike >= (MIN_PREMIUM_PCT_OF_STRIKE * 100):
                    near_miss_score = (mid / min_credit) * (premium_pct_of_strike / 1.0) * 50  # Rough score
                    near_miss_msg = f"⚠️ {ticker} ${strike:.0f} CALL — {exp_str}\n  Credit: ${mid:.2f} ({mid*100:.0f}/contract) ✓\n  Cushion: {cushion_pct:.1%} ✓\n  Problem: Only {oi} OI (need ≥500)"
                    if not near_miss_candidate or near_miss_score > near_miss_candidate[0]:
                        near_miss_candidate = (near_miss_score, tier, near_miss_msg)
                continue
            
            spread_pct = (ask - bid) / bid
            if spread_pct > MAX_BID_ASK_SPREAD_PCT:
                continue

            mid = (bid + ask) / 2
            premium_per_contract = mid * 100

            # Minimum credit filter
            if mid < min_credit:
                # Near-miss: has good strike/cushion but low credit
                premium_pct_of_strike = (mid / strike) * 100
                near_miss_score = (mid / min_credit) * (cushion_pct / min_cushion) * 50
                near_miss_msg = f"⚠️ {ticker} ${strike:.0f} CALL — {exp_str}\n  Credit: ${mid:.2f} ({mid*100:.0f}/contract) - need ${min_credit*100:.0f}\n  Cushion: {cushion_pct:.1%} ✓\n  Gap: ${(min_credit - mid)*100:.0f} more needed"
                if not near_miss_candidate or near_miss_score > near_miss_candidate[0]:
                    near_miss_candidate = (near_miss_score, tier, near_miss_msg)
                continue

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

    # Sort by score descending
    hits.sort(key=lambda x: x[0], reverse=True)
    
    # Return top 3 hits and best near-miss (if any)
    hit_messages = [msg for _, msg in hits[:3]]
    
    # Format near_miss if we have one: (tier, score, message)
    near_miss_return = None
    if near_miss_candidate:
        score, tier, msg = near_miss_candidate
        near_miss_return = (tier, score, msg)
    
    return hit_messages, near_miss_return
