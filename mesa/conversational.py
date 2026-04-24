"""Conversational prospector agent for Telegram.

Parses natural language screening criteria from Telegram messages,
scans watchlist + S&P 500, returns top 3 candidates.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

import yfinance as yf

from mesa.telegram_send import send
from mesa.scoring import Candidate, format_report
from mesa.market_data import get_ticker_data, get_options_chain, get_expirations

log = logging.getLogger(__name__)

# Priority watchlist (quality companies, $90-$110 sweet spot)
PRIORITY_WATCHLIST = [
    "JPM", "ABBV", "KO", "DDOG", "MSFT", "AAPL", "AMZN",
    "GOOGL", "META", "V", "MA", "UNH", "PG", "JNJ",
    "WMT", "HD", "NET", "SNOW", "CRM", "NOW",
]

# Broader watchlist (fallback if priority has no hits)
FALLBACK_WATCHLIST = [
    "PLTR", "AMD", "UBER", "NFLX", "ABNB", "SHOP",
    "PINS", "DASH", "ROKU", "SMCI",
]

# EXCLUSION LISTS
CRYPTO_EXCLUSION = {"MARA", "RIOT", "COIN", "MSTR", "CLSK", "HOOD"}
ACCOUNTING_ISSUES = set()

# Price constraints (wheel strategy: $90-$110 sweet spot)
MIN_STOCK_PRICE = 90.0
MAX_STOCK_PRICE = 110.0
MIN_STOCK_PRICE_WIDE = 50.0
MAX_STOCK_PRICE_WIDE = 200.0
MIN_MARKET_CAP = 1e10

MIN_ANNUALIZED_RETURN = 0.15
MIN_OPEN_INTEREST = 100
MAX_BID_ASK_SPREAD_PCT = 0.20
TARGET_DTE_MIN = 30
TARGET_DTE_MAX = 60


def parse_criteria(msg: str) -> Optional[dict]:
    """Parse natural language screening request."""
    msg_lower = msg.lower()
    
    # Check for command keywords
    if not any(kw in msg_lower for kw in ["find", "screen", "check", "show", "look", "search", "scan"]):
        return None
    
    criteria = {
        "watchlist": [],
        "price_min": MIN_STOCK_PRICE,
        "price_max": MAX_STOCK_PRICE,
        "iv_threshold": 0,
        "exclude_earnings": False,
        "limit": 3,
    }
    
    # Price range: default $90-$110, or /scan wide for $50-$200
    if "wide" in msg_lower:
        criteria["price_min"] = MIN_STOCK_PRICE_WIDE
        criteria["price_max"] = MAX_STOCK_PRICE_WIDE
    
    # User-specified tickers (check XYZAB format)
    ticker_matches = re.findall(r'\b([A-Z]{1,5})\b', msg)
    if ticker_matches:
        valid_tickers = [t for t in ticker_matches if t not in ["THE", "AND", "FOR", "WITH", "SHOW", "FIND", "OVER", "UNDER", "HIGH", "LOW", "BETWEEN", "TOP", "SCREEN", "CHECK"]]
        if valid_tickers:
            criteria["watchlist"] = valid_tickers[:5]
    
    # Earnings exclusion
    if any(kw in msg_lower for kw in ["no earnings", "exclude earnings", "skip earnings"]):
        criteria["exclude_earnings"] = True
    
    # Limit
    limit_match = re.search(r'(?:top|best|show)\s*(\d+)', msg_lower)
    if limit_match:
        criteria["limit"] = int(limit_match.group(1))
    
    return criteria


def scan_ticker(ticker: str, today: date, price_max: float = MAX_STOCK_PRICE) -> Optional[dict]:
    """Scan single ticker for CSP opportunities with retry logic."""
    
    # EXCLUSION FILTERS
    if ticker in CRYPTO_EXCLUSION:
        log.debug(f"Skipping {ticker}: crypto-related")
        return None
    
    if ticker in ACCOUNTING_ISSUES:
        log.debug(f"Skipping {ticker}: known accounting issues")
        return None
    
    # Fetch ticker data with retry logic
    data = get_ticker_data(ticker)
    if not data:
        log.debug(f"Skipping {ticker}: failed to fetch data")
        return None
    
    price = data['price']
    market_cap = data['market_cap']
    
    # Price range filter
    if price < MIN_STOCK_PRICE:
        log.debug(f"Skipping {ticker}: ${price:.2f} < ${MIN_STOCK_PRICE}")
        return None
    
    if price > price_max:
        log.debug(f"Skipping {ticker}: ${price:.2f} > ${price_max}")
        return None
    
    # Market cap filter
    if market_cap and market_cap < MIN_MARKET_CAP:
        log.debug(f"Skipping {ticker}: market cap ${market_cap/1e9:.1f}B < $10B")
        return None
    
    # Get option expirations with retry logic
    expiries = get_expirations(ticker)
    if not expiries:
        log.debug(f"Skipping {ticker}: no options available")
        return None
    
    best_hit = None
    best_return = 0
    
    for exp_str in expiries:
        try:
            exp_date = date.fromisoformat(exp_str)
            dte = (exp_date - today).days
            if dte < TARGET_DTE_MIN or dte > TARGET_DTE_MAX:
                continue
            
            # Fetch options chain with retry logic
            chain = get_options_chain(ticker, exp_str)
            if not chain:
                continue
            
            puts = chain['puts']
            
            for _, row in puts.iterrows():
                try:
                    strike = float(row["strike"])
                    
                    # OTM puts only
                    if strike >= price:
                        continue
                    
                    # Moneyness filter
                    moneyness = strike / price
                    if moneyness < 0.80 or moneyness > 0.95:
                        continue
                    
                    bid = float(row.get("bid", 0))
                    ask = float(row.get("ask", 0))
                    if bid <= 0 or ask <= 0:
                        continue
                    
                    oi = int(row.get("openInterest", 0) or 0)
                    if oi < MIN_OPEN_INTEREST:
                        continue
                    
                    # Liquidity
                    spread_pct = (ask - bid) / bid
                    if spread_pct > MAX_BID_ASK_SPREAD_PCT:
                        continue
                    
                    # Return calculation
                    mid = (bid + ask) / 2
                    premium_per_contract = mid * 100
                    capital_required = strike * 100
                    raw_return = premium_per_contract / capital_required
                    annualized = raw_return * (365 / dte) if dte > 0 else 0
                    
                    if annualized < MIN_ANNUALIZED_RETURN:
                        continue
                    
                    breakeven = strike - mid
                    cushion_pct = (price - breakeven) / price
                    
                    hit = {
                        "ticker": ticker,
                        "price": price,
                        "strike": strike,
                        "expiry": exp_str,
                        "dte": dte,
                        "premium_per_contract": premium_per_contract,
                        "annualized": annualized,
                        "breakeven": breakeven,
                        "cushion_pct": cushion_pct,
                        "oi": oi,
                        "spread_pct": spread_pct,
                    }
                    
                    if annualized > best_return:
                        best_hit = hit
                        best_return = annualized
                
                except (ValueError, TypeError, KeyError) as e:
                    log.debug(f"{ticker}: error processing put row: {e}")
                    continue
        
        except Exception as e:
            log.debug(f"{ticker} {exp_str}: {e}")
            continue
    
    return best_hit


def screen(criteria: dict) -> list:
    """Screen tickers and return candidates."""
    today = date.today()
    results = []
    price_max = criteria.get("price_max", MAX_STOCK_PRICE)
    
    # User-specified tickers
    if criteria["watchlist"]:
        tickers_to_scan = criteria["watchlist"]
        log.info(f"Screening user-specified: {tickers_to_scan}")
    else:
        tickers_to_scan = PRIORITY_WATCHLIST
        log.info("Screening priority watchlist...")
    
    for ticker in tickers_to_scan:
        hit = scan_ticker(ticker, today, price_max)
        if hit:
            results.append(hit)
    
    # Expand to fallback if needed
    if not results and not criteria["watchlist"]:
        log.info("Expanding to fallback watchlist...")
        for ticker in FALLBACK_WATCHLIST:
            hit = scan_ticker(ticker, today, price_max)
            if hit:
                results.append(hit)
    
    results.sort(key=lambda x: x["annualized"], reverse=True)
    return results[:criteria.get("limit", 3)]


def handle_message(message: str) -> None:
    """Parse message, screen, and send traffic light report."""
    criteria = parse_criteria(message)
    
    if not criteria:
        send("🔭 I'm Mesa Prospector. Try: *find quality puts*, *scan green*, *check JPM DDOG*, etc.")
        return
    
    log.info(f"Screening: {criteria}")
    send("🔍 Scanning...")
    
    try:
        results = screen(criteria)
        
        if not results:
            send("No opportunities found. Check back soon!")
            return
        
        # Convert to Candidates
        candidates = []
        for r in results:
            market_cap = None
            try:
                data = get_ticker_data(r["ticker"])
                if data:
                    market_cap = data.get('market_cap')
            except:
                pass
            
            candidate = Candidate(
                ticker=r["ticker"],
                price=r["price"],
                strike=r["strike"],
                expiry=r["expiry"],
                dte=r["dte"],
                premium=r["premium_per_contract"],
                annualized=r["annualized"],
                breakeven=r["breakeven"],
                cushion_pct=r["cushion_pct"],
                oi=r["oi"],
                spread_pct=r["spread_pct"],
                market_cap=market_cap,
            )
            candidates.append(candidate)
        
        report = format_report(candidates, limit_green=criteria.get("limit", 3))
        send(report)
    
    except Exception as e:
        log.error(f"Error: {e}", exc_info=True)
        send(f"⚠️ Error: {str(e)[:100]}")


if __name__ == "__main__":
    handle_message("find quality puts")
