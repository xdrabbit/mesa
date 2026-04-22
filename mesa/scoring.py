"""Traffic light scoring system for prospector candidates.

Each candidate gets rated:
- 🟢 GREEN: Execute (meets all criteria, good timing)
- 🟡 YELLOW: Watch (right stock, wrong moment - revisit later)
- 🔴 RED: Skip (wrong stock forever - fundamental issues)
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

import yfinance as yf

log = logging.getLogger(__name__)

# Constants
CRYPTO_EXCLUSION = {"MARA", "RIOT", "COIN", "MSTR", "CLSK", "HOOD"}
ACCOUNTING_ISSUES = set()
MIN_STOCK_PRICE = 50.0
MAX_STOCK_PRICE = 110.0
MIN_MARKET_CAP = 1e10  # $10B
EARNINGS_LOOKBACK_DAYS = 21


class Candidate:
    """Scored prospector candidate."""
    
    def __init__(
        self,
        ticker: str,
        price: float,
        strike: float,
        expiry: str,
        dte: int,
        premium: float,
        annualized: float,
        breakeven: float,
        cushion_pct: float,
        oi: int,
        spread_pct: float,
        market_cap: Optional[float] = None,
    ):
        self.ticker = ticker
        self.price = price
        self.strike = strike
        self.expiry = expiry
        self.dte = dte
        self.premium = premium
        self.annualized = annualized
        self.breakeven = breakeven
        self.cushion_pct = cushion_pct
        self.oi = oi
        self.spread_pct = spread_pct
        self.market_cap = market_cap
        
        self.color = None  # 🟢, 🟡, 🔴
        self.reason = None
        self.extra_info = None  # For YELLOW: earnings date, etc.
        
        self._score()
    
    def _score(self) -> None:
        """Assign color and reason."""
        
        # 🔴 RED checks (wrong stock forever)
        if self.ticker in CRYPTO_EXCLUSION:
            self.color = "🔴"
            self.reason = f"crypto miner, wrong kind of volatility, removed from scan"
            return
        
        if self.ticker in ACCOUNTING_ISSUES:
            self.color = "🔴"
            self.reason = "SEC investigation / accounting issues"
            return
        
        if self.price < MIN_STOCK_PRICE:
            self.color = "🔴"
            self.reason = f"stock under ${MIN_STOCK_PRICE}, wrong volatility profile"
            return
        
        if self.market_cap and self.market_cap < MIN_MARKET_CAP:
            self.color = "🔴"
            self.reason = f"market cap ${self.market_cap/1e9:.1f}B < $10B"
            return
        
        # 🟡 YELLOW checks (right stock, wrong moment)
        yellow_flags = []
        
        # Too few DTE (timing issue)
        if self.dte < 21:
            yellow_flags.append(f"{self.dte}d to expiry")
        
        # Check earnings (simplified - would need calendar API)
        # For now, we flag this manually based on known dates
        if self._has_earnings_soon():
            self.extra_info = self._get_earnings_info()
            yellow_flags.append("earnings soon")
        
        # High spread = liquidity concern
        if self.spread_pct > 0.10:
            yellow_flags.append(f"{self.spread_pct:.0%} spread")
        
        # Exceptional premium (even if risky, it's a good stock worth noting)
        if self.annualized > 0.50:
            yellow_flags.append("exceptional premium")
        
        if yellow_flags:
            self.color = "🟡"
            self.reason = f"great wheel candidate, {', '.join(yellow_flags)}"
            if not self.extra_info:
                self.extra_info = f"revisit later, ${self.premium:.0f} premium waiting. Set reminder."
            return
        
        # 🟢 GREEN (execute)
        self.color = "🟢"
        self.reason = "execute candidate"
    
    def _has_earnings_soon(self) -> bool:
        """Check if earnings within 21 days (simplified)."""
        # In production, would query calendar API
        # For now, return False (TODO: integrate earnings calendar)
        return False
    
    def _get_earnings_info(self) -> str:
        """Return earnings date info."""
        # Placeholder
        return "earnings soon, revisit after"
    
    def format_green(self) -> str:
        """Format as GREEN candidate."""
        return (
            f"🟢 *{self.ticker}* ${self.price:.2f}\n"
            f"  Strike: ${self.strike:.0f} | Exp: {self.expiry} ({self.dte}d)\n"
            f"  Premium: ${self.premium:.0f} | Return: {self.annualized:.0%}\n"
            f"  Breakeven: ${self.breakeven:.2f} | Cushion: {self.cushion_pct:.1%}"
        )
    
    def format_yellow(self) -> str:
        """Format as YELLOW candidate."""
        return (
            f"🟡 *{self.ticker}* ${self.price:.2f} — {self.reason}, "
            f"{self.extra_info}"
        )
    
    def format_red(self) -> str:
        """Format as RED candidate (one-liner)."""
        return f"🔴 {self.ticker} — {self.reason}"
    
    def format(self) -> str:
        """Return formatted candidate based on color."""
        if self.color == "🟢":
            return self.format_green()
        elif self.color == "🟡":
            return self.format_yellow()
        elif self.color == "🔴":
            return self.format_red()
        else:
            return f"? {self.ticker} — unknown color"


def score_candidates(candidates: list[Candidate]) -> dict:
    """Organize candidates by color.
    
    Returns:
    {
        "🟢": [green_candidates],
        "🟡": [yellow_candidates],
        "🔴": [red_candidates],
    }
    """
    organized = {
        "🟢": [],
        "🟡": [],
        "🔴": [],
    }
    
    for candidate in candidates:
        organized[candidate.color].append(candidate)
    
    # Sort each group by annualized return (descending)
    for color in ["🟢", "🟡"]:
        organized[color].sort(key=lambda c: c.annualized, reverse=True)
    
    # RED stays in order (doesn't matter)
    
    return organized


def format_report(candidates: list[Candidate], limit_green: int = 3) -> str:
    """Format full report with three colors."""
    organized = score_candidates(candidates)
    
    lines = ["🚦 *Prospector Report*\n"]
    
    # 🟢 GREEN (execute now)
    if organized["🟢"]:
        lines.append("*🟢 GREEN — Execute*")
        for c in organized["🟢"][:limit_green]:
            lines.append(c.format())
        lines.append("")
    else:
        lines.append("*🟢 GREEN — None found*\n")
    
    # 🟡 YELLOW (right stock, wrong moment)
    if organized["🟡"]:
        lines.append("*🟡 YELLOW — Right Stock, Wrong Moment*")
        for c in organized["🟡"]:
            lines.append(c.format())
        lines.append("")
    
    # 🔴 RED (collapsed - wrong stock forever)
    if organized["🔴"]:
        lines.append("*🔴 RED — Wrong Stock Forever*")
        for c in organized["🔴"]:
            lines.append(c.format())
    
    return "\n".join(lines)
