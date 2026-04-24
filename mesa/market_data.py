#!/usr/bin/env python3

"""
Market data fetching with retry logic and error handling.

Wraps yfinance calls with:
- Automatic retry (max 2 attempts)
- Graceful degradation on failure
- Detailed logging
"""

import logging
import yfinance as yf
from datetime import date
from typing import Optional

log = logging.getLogger(__name__)

MAX_RETRIES = 2
RETRY_DELAY = 1  # seconds


def get_ticker_data(ticker: str, max_attempts: int = MAX_RETRIES) -> Optional[dict]:
    """
    Fetch ticker data with retry logic.
    
    Returns dict with:
    - price: current price
    - market_cap: market cap
    - history: price history
    
    Returns None if all attempts fail.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            t = yf.Ticker(ticker)
            
            # Try to get price history
            hist = t.history(period="1d")
            if hist.empty:
                log.warning(f"{ticker}: no price history on attempt {attempt}")
                continue
            
            price = float(hist["Close"].iloc[-1])
            if price <= 0:
                log.warning(f"{ticker}: invalid price ${price} on attempt {attempt}")
                continue
            
            # Try to get market cap
            market_cap = None
            try:
                info = t.info
                if info:
                    market_cap = info.get("marketCap")
            except Exception as e:
                log.debug(f"{ticker}: could not fetch market cap: {e}")
            
            log.debug(f"{ticker}: success on attempt {attempt}")
            
            return {
                'price': price,
                'market_cap': market_cap,
                'history': hist
            }
            
        except Exception as e:
            log.warning(f"{ticker}: attempt {attempt} failed: {type(e).__name__}: {str(e)[:80]}")
            if attempt < max_attempts:
                continue
            else:
                log.error(f"{ticker}: all {max_attempts} attempts failed, skipping")
                return None
    
    return None


def get_options_chain(ticker: str, expiry: str, max_attempts: int = MAX_RETRIES) -> Optional[dict]:
    """
    Fetch options chain with retry logic.
    
    Returns dict with 'calls' and 'puts' DataFrames.
    Returns None if all attempts fail.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            t = yf.Ticker(ticker)
            chain = t.option_chain(expiry)
            
            if chain is None or chain.calls is None or chain.puts is None:
                log.warning(f"{ticker} {expiry}: empty chain on attempt {attempt}")
                continue
            
            log.debug(f"{ticker} {expiry}: success on attempt {attempt}")
            
            return {
                'calls': chain.calls,
                'puts': chain.puts
            }
            
        except Exception as e:
            log.warning(f"{ticker} {expiry}: attempt {attempt} failed: {type(e).__name__}: {str(e)[:80]}")
            if attempt < max_attempts:
                continue
            else:
                log.error(f"{ticker} {expiry}: all {max_attempts} attempts failed, skipping")
                return None
    
    return None


def get_expirations(ticker: str, max_attempts: int = MAX_RETRIES) -> Optional[list]:
    """
    Fetch option expirations with retry logic.
    
    Returns list of expiration date strings.
    Returns empty list if all attempts fail.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            t = yf.Ticker(ticker)
            options = t.options
            
            if not options or len(options) == 0:
                log.warning(f"{ticker}: no options on attempt {attempt}")
                continue
            
            log.debug(f"{ticker}: found {len(options)} expirations on attempt {attempt}")
            return options
            
        except Exception as e:
            log.warning(f"{ticker}: attempt {attempt} failed: {type(e).__name__}: {str(e)[:80]}")
            if attempt < max_attempts:
                continue
            else:
                log.error(f"{ticker}: all {max_attempts} attempts failed, returning empty")
                return []
    
    return []
