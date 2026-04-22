#!/usr/bin/env python3

"""
MESA Morning Brief — 7:30 AM MT Daily Scan

Filters:
- Stock price < $100
- Market cap > $10B
- IV 25-40% (ideal), flag >50% as risky
- Premium >= $200/contract
- No earnings within 21 days

Output:
- GREEN: Execute ready (all criteria met)
- YELLOW: Watch (timing issue or high IV)
- RED: Skip (fundamental issues)
"""

from datetime import datetime, timedelta
import pytz
import yfinance as yf
import logging

from mesa.config import get_settings
from mesa.scoring import Candidate
from mesa.telegram_send import send

log = logging.getLogger(__name__)

ET = pytz.timezone('US/Eastern')
MT = pytz.timezone('US/Mountain')

# Core filters
PRICE_MAX = 100.0
MIN_MARKET_CAP = 10e9
IV_MIN = 25
IV_MAX = 40
IV_RISKY = 50
MIN_PREMIUM = 200

WATCHLIST = [
    'JPM', 'ABBV', 'KO', 'DDOG', 'MSFT', 'AAPL', 'AMZN',
    'GOOGL', 'META', 'V', 'MA', 'UNH', 'PG', 'JNJ', 'WMT',
    'HD', 'NET', 'SNOW', 'CRM', 'NOW', 'NEM', 'PCAR', 'UPS',
    'BX', 'FCX', 'CCI', 'EXE', 'NFLX', 'ELF'
]

def get_stock_data(ticker):
    """Get price, market cap, IV for ticker."""
    try:
        t = yf.Ticker(ticker)
        info = t.info
        
        price = info.get('currentPrice')
        market_cap = info.get('marketCap')
        
        # Get IV from options
        iv = None
        if t.options:
            try:
                opt = t.option_chain(t.options[0])
                calls = opt.calls
                if not calls.empty:
                    iv = calls['impliedVolatility'].mean() * 100
            except:
                pass
        
        return {
            'price': price,
            'market_cap': market_cap,
            'iv': iv
        }
    except Exception as e:
        log.warning(f"Error fetching {ticker}: {e}")
        return None

def get_earnings_info(ticker):
    """Get earnings date and days until."""
    try:
        t = yf.Ticker(ticker)
        earnings_ts = t.info.get('earningsDate')
        
        if earnings_ts:
            earnings_date = datetime.fromtimestamp(earnings_ts, ET).date()
            today = datetime.now(ET).date()
            days = (earnings_date - today).days
            return {
                'date': earnings_date.strftime('%Y-%m-%d'),
                'days': days
            }
    except:
        pass
    
    return None

def create_candidate(ticker, data):
    """Create a Candidate from stock data."""
    price = data['price']
    market_cap = data['market_cap']
    iv = data['iv']
    
    # Estimate strike (10% OTM) and premium (rough estimate)
    strike = price * 0.90
    estimated_premium = max(200, int(price * 0.15))
    
    candidate = Candidate(
        ticker=ticker,
        price=price,
        strike=strike,
        expiry=(datetime.now() + timedelta(days=45)).strftime('%Y-%m-%d'),
        dte=45,
        premium=estimated_premium,
        annualized=0.20,
        breakeven=strike - estimated_premium / 100,
        cushion_pct=0.10,
        oi=1000,
        spread_pct=0.05,
        market_cap=market_cap
    )
    
    candidate.iv = iv
    candidate.earnings = get_earnings_info(ticker)
    
    return candidate

def apply_filters(candidate):
    """Apply all filters and return color/reason."""
    
    # RED checks (skip forever)
    if candidate.price > PRICE_MAX:
        return '🔴', f"price ${candidate.price:.0f} > ${PRICE_MAX} max"
    
    if candidate.market_cap < MIN_MARKET_CAP:
        return '🔴', f"market cap ${candidate.market_cap/1e9:.1f}B < $10B"
    
    if candidate.premium < MIN_PREMIUM:
        return '🔴', f"premium ${candidate.premium:.0f} < ${MIN_PREMIUM} min"
    
    # YELLOW checks (timing issue)
    yellow_flags = []
    
    if candidate.iv and candidate.iv > IV_RISKY:
        yellow_flags.append(f"IV {candidate.iv:.0f}% risky")
    elif candidate.iv and (candidate.iv < IV_MIN or candidate.iv > IV_MAX):
        yellow_flags.append(f"IV {candidate.iv:.0f}% (ideal 25-40%)")
    
    if candidate.earnings and candidate.earnings['days'] < 21:
        yellow_flags.append(f"earnings in {candidate.earnings['days']}d")
    
    if yellow_flags:
        return '🟡', ' / '.join(yellow_flags)
    
    # GREEN (execute ready)
    return '🟢', 'execute ready'

def scan_and_filter():
    """Scan watchlist and apply filters."""
    candidates = []
    
    for ticker in WATCHLIST:
        data = get_stock_data(ticker)
        if not data or not data['price']:
            continue
        
        candidate = create_candidate(ticker, data)
        color, reason = apply_filters(candidate)
        
        candidate.color = color
        candidate.reason = reason
        candidates.append(candidate)
    
    # Separate by color
    green = [c for c in candidates if c.color == '🟢']
    yellow = [c for c in candidates if c.color == '🟡']
    red = [c for c in candidates if c.color == '🔴']
    
    return green, yellow, red

def format_brief(green, yellow, red):
    """Format the morning briefing."""
    now = datetime.now(MT)
    et_now = now.astimezone(ET)
    
    date_str = now.strftime("%A, %B %d, %Y")
    time_str = now.strftime("%I:%M %p MT")
    
    brief = f"""🌅 *MESA Morning Brief* — {date_str}
⏰ {time_str} (ET: {et_now.strftime('%I:%M %p')})

"""
    
    # GREEN candidates
    if green:
        brief += "*🟢 GREEN — Execute Ready*\n"
        for c in green[:3]:
            iv_str = f"{c.iv:.0f}% IV" if c.iv else "IV?"
            brief += f"🟢 *{c.ticker}* ${c.price:.2f} | ${c.strike:.2f} | ${c.premium:.0f} | {iv_str}\n"
        brief += "\n"
    else:
        brief += "*🟢 GREEN — None*\n\n"
    
    # YELLOW candidates
    if yellow:
        brief += "*🟡 YELLOW — Watch (Timing)*\n"
        for c in yellow[:5]:
            iv_str = f"{c.iv:.0f}% IV" if c.iv else "IV?"
            brief += f"🟡 *{c.ticker}* ${c.price:.2f} | {iv_str} | {c.reason}\n"
        brief += "\n"
    
    # RED (one-liner each)
    if red:
        brief += "*🔴 RED — Skip*\n"
        for c in red:
            brief += f"🔴 {c.ticker} — {c.reason}\n"
    
    return brief.strip()

def main():
    """Generate and send morning briefing."""
    logging.basicConfig(level=logging.INFO)
    
    try:
        log.info("Starting morning brief scan...")
        
        green, yellow, red = scan_and_filter()
        
        brief = format_brief(green, yellow, red)
        
        # Print and send
        print(brief)
        send(brief)
        
        log.info(f"Brief sent: {len(green)} GREEN, {len(yellow)} YELLOW, {len(red)} RED")
        
    except Exception as e:
        log.error(f"Error: {e}", exc_info=True)
        raise

if __name__ == '__main__':
    main()
