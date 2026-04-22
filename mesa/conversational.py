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

log = logging.getLogger(__name__)

# Priority watchlist (quality companies, $50-$110 sweet spot)
# Screen these FIRST before going broader
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
# Crypto-related (too volatile, wrong risk profile)
CRYPTO_EXCLUSION = {"MARA", "RIOT", "COIN", "MSTR", "CLSK", "HOOD"}

# Companies with known accounting issues or SEC investigations
# (Update as needed based on news/SEC filings)
ACCOUNTING_ISSUES = set()  # Add as needed: {"XXX", "YYY"}

# Price constraints (wheel strategy)
MIN_STOCK_PRICE = 50.0    # Too small/volatile below $50
MAX_STOCK_PRICE = 110.0   # Capital constraint: $10k / 100 shares = $100 strike max
MIN_MARKET_CAP = 1e10     # $10 billion minimum

MIN_ANNUALIZED_RETURN = 0.15
MIN_OPEN_INTEREST = 100
MAX_BID_ASK_SPREAD_PCT = 0.20
TARGET_DTE_MIN = 30
TARGET_DTE_MAX = 60


def parse_criteria(message: str) -> dict:
    """Parse Telegram message for screening criteria.
    
    Returns dict with keys:
    - watchlist: list of tickers to check (empty = use default)
    - price_min, price_max: price range filter
    - iv_threshold: minimum IV% (0-100, 0 = any)
    - exclude_earnings: skip tickers with earnings soon
    - limit: max results (default 3)
    """
    criteria = {
        "watchlist": [],
        "price_min": 0,
        "price_max": MAX_STOCK_PRICE,
        "iv_threshold": 0,
        "exclude_earnings": False,
        "limit": 3,
    }

    msg = message.lower().strip()
    
    # ── Check for screening intent ──
    screening_intent = any(
        kw in msg for kw in ["find", "screen", "show me", "prospects", "what about", "check"]
    )
    if not screening_intent and not re.search(r'\$?\d+', msg):
        return None  # Not a screening request
    
    # ── Extract specific tickers ──
    # "check DDOG" or "how's JPM" or "DDOG NFLX PLTR"
    ticker_pattern = r'\b([A-Z]{1,5})\b'
    matches = re.findall(ticker_pattern, msg)
    if matches:
        criteria["watchlist"] = [m.upper() for m in matches]
        log.info(f"Parsed tickers: {criteria['watchlist']}")
    
    # ── Price range ──
    # "under $50", "between $20 and $80", "$30 to $60"
    under_match = re.search(r'under\s*\$?(\d+)', msg)
    if under_match:
        criteria["price_max"] = float(under_match.group(1))
    
    between_match = re.search(r'between\s*\$?(\d+)\s*(?:and|-)\s*\$?(\d+)', msg)
    if between_match:
        criteria["price_min"] = float(between_match.group(1))
        criteria["price_max"] = float(between_match.group(2))
    
    # ── IV threshold ──
    # "IV above 40%", "high IV", etc.
    iv_match = re.search(r'IV\s*(?:above|over|>)?\s*(\d+)', msg, re.IGNORECASE)
    if iv_match:
        criteria["iv_threshold"] = float(iv_match.group(1))
    
    # ── Exclude earnings ──
    if any(kw in msg for kw in ["no earnings", "exclude earnings", "skip earnings"]):
        criteria["exclude_earnings"] = True
    
    # ── Limit (top N) ──
    limit_match = re.search(r'(?:top|best|show)\s*(\d+)', msg)
    if limit_match:
        criteria["limit"] = int(limit_match.group(1))
    
    return criteria


def scan_ticker(ticker: str, today: date, price_max: float = MAX_STOCK_PRICE) -> Optional[dict]:
    """Scan single ticker for CSP opportunities.
    
    Applies exclusion filters + quality checks before screening.
    Returns best candidate dict or None.
    """
    # EXCLUSION FILTERS
    if ticker in CRYPTO_EXCLUSION:
        log.debug(f"Skipping {ticker}: crypto-related")
        return None
    
    if ticker in ACCOUNTING_ISSUES:
        log.debug(f"Skipping {ticker}: known accounting issues")
        return None
    
    try:
        t = yf.Ticker(ticker)
        price_hist = t.history(period="1d")
        if price_hist.empty:
            log.debug(f"Skipping {ticker}: no price data")
            return None
        
        price = float(price_hist["Close"].iloc[-1])
        
        # Price range filter (wheel strategy: $50-$110 sweet spot)
        if price < MIN_STOCK_PRICE:
            log.debug(f"Skipping {ticker}: ${price:.2f} < ${MIN_STOCK_PRICE} (too small)")
            return None
        
        if price > price_max:
            log.debug(f"Skipping {ticker}: ${price:.2f} > ${price_max} (too expensive)")
            return None
        
        # Market cap filter ($10B minimum)
        try:
            info = t.info
            market_cap = info.get("marketCap")
            if market_cap and market_cap < MIN_MARKET_CAP:
                log.debug(f"Skipping {ticker}: market cap ${market_cap/1e9:.1f}B < $10B")
                return None
        except Exception as e:
            log.debug(f"Could not check market cap for {ticker}: {e}")
        
        # Get options
        expiries = t.options
        best_hit = None
        best_return = 0
        
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
                
                # OTM puts only
                if strike >= price:
                    continue
                
                # Delta filter (moneyness 80-95%)
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
                
                # Annualized return
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
                    "mid": mid,
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
        
        return best_hit
    
    except Exception as e:
        log.error(f"Error scanning {ticker}: {e}")
        return None


def screen(criteria: dict) -> list:
    """Screen tickers and return top candidates.
    
    Strategy:
    1. If user specified tickers, use those
    2. Otherwise, screen priority watchlist first
    3. If no hits in priority, expand to fallback watchlist
    """
    today = date.today()
    results = []
    
    # User-specified tickers take priority
    if criteria["watchlist"]:
        tickers_to_scan = criteria["watchlist"]
        log.info(f"Screening user-specified tickers: {tickers_to_scan}")
    else:
        # Strategy: priority first, then fallback
        log.info("Screening priority watchlist first...")
        tickers_to_scan = PRIORITY_WATCHLIST
    
    # Initial scan
    for ticker in tickers_to_scan:
        hit = scan_ticker(ticker, today, criteria["price_max"])
        if hit and hit["price"] >= criteria["price_min"]:
            results.append(hit)
    
    # If no hits and we used priority watchlist, expand to fallback
    if not results and not criteria["watchlist"]:
        log.info("No hits in priority watchlist, expanding to fallback...")
        for ticker in FALLBACK_WATCHLIST:
            hit = scan_ticker(ticker, today, criteria["price_max"])
            if hit and hit["price"] >= criteria["price_min"]:
                results.append(hit)
    
    # Sort by annualized return, descending
    results.sort(key=lambda x: x["annualized"], reverse=True)
    
    return results[:criteria["limit"]]


def format_result(hit: dict, market_cap: Optional[float] = None) -> str:
    """Convert hit dict to Candidate and return formatted string."""
    candidate = Candidate(
        ticker=hit["ticker"],
        price=hit["price"],
        strike=hit["strike"],
        expiry=hit["expiry"],
        dte=hit["dte"],
        premium=hit["premium_per_contract"],
        annualized=hit["annualized"],
        breakeven=hit["breakeven"],
        cushion_pct=hit["cushion_pct"],
        oi=hit["oi"],
        spread_pct=hit["spread_pct"],
        market_cap=market_cap,
    )
    return candidate.format()


def handle_message(message: str) -> None:
    """Parse message, screen, and send traffic light report."""
    criteria = parse_criteria(message)
    
    if not criteria:
        # Not a screening request
        send("I'm your Mesa prospector agent. Try: *find quality puts*, *screen under $80*, *check JPM*, etc.")
        return
    
    log.info(f"Screening with criteria: {criteria}")
    send("🚦 Scanning..." )
    
    try:
        results = screen(criteria)
        
        if not results:
            send("No opportunities found. No GREEN or YELLOW candidates at the moment.")
            return
        
        # Convert results to Candidate objects for scoring
        candidates = []
        for r in results:
            # Try to get market cap (optional, may fail)
            market_cap = None
            try:
                t = yf.Ticker(r["ticker"])
                market_cap = t.info.get("marketCap")
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
        
        # Format as traffic light report
        report = format_report(candidates, limit_green=criteria["limit"])
        send(report)
    
    except Exception as e:
        log.error(f"Screening error: {e}", exc_info=True)
        send(f"Error screening: {e}")


if __name__ == "__main__":
    # Test
    test_msg = "find me puts under $80 with IV above 30%"
    handle_message(test_msg)
