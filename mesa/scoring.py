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
MIN_STOCK_PRICE = 80.0
MAX_STOCK_PRICE = 140.0
MIN_MARKET_CAP = 1e10  # $10B
MIN_IV = 0.35
MAX_IV = 0.65
MIN_DAYS_TO_EARNINGS = 14


class Candidate:
    """Scored prospector candidate."""
    
    def __init__(
        self,
        ticker: str,
        price: float,
        strike: float,
        expiry: str,
        dte: int,
        delta: float,
        premium: float,
        premium_pct_of_strike: float,
        annualized: float,
        breakeven: float,
        cushion_pct: float,
        oi: int,
        spread_pct: float,
        iv: Optional[float] = None,
        market_cap: Optional[float] = None,
        days_to_earnings: Optional[int] = None,
    ):
        self.ticker = ticker
        self.price = price
        self.strike = strike
        self.expiry = expiry
        self.dte = dte
        self.delta = delta
        self.premium = premium
        self.premium_pct_of_strike = premium_pct_of_strike
        self.annualized = annualized
        self.breakeven = breakeven
        self.cushion_pct = cushion_pct
        self.oi = oi
        self.spread_pct = spread_pct
        self.iv = iv
        self.market_cap = market_cap
        self.days_to_earnings = days_to_earnings
        
        self.color = None  # 🟢, 🟡, 🔴
        self.reason = None
        self.extra_info = None  # For YELLOW: earnings date, etc.
        self.score = 0.0  # 0-100 ranking score
        
        self._score()
    
    def _score(self) -> None:
        """Assign color, reason, and score (0-100).
        
        Three-color traffic light:
        - 🔴 RED: Fundamental issues (skip forever)
        - 🟡 YELLOW: Right stock, wrong timing (set reminder)
        - 🟢 GREEN: Execute now (all filters pass)
        """
        
        # 🔴 RED checks (wrong stock forever)
        if self.ticker in CRYPTO_EXCLUSION:
            self.color = "🔴"
            self.reason = "crypto-related"
            self.score = 0
            return
        
        if self.ticker in ACCOUNTING_ISSUES:
            self.color = "🔴"
            self.reason = "SEC investigation / accounting issues"
            self.score = 0
            return
        
        if self.price < MIN_STOCK_PRICE or self.price > MAX_STOCK_PRICE:
            self.color = "🔴"
            self.reason = f"price ${self.price:.0f} outside ${MIN_STOCK_PRICE}-${MAX_STOCK_PRICE} range"
            self.score = 0
            return
        
        if self.market_cap and self.market_cap < MIN_MARKET_CAP:
            self.color = "🔴"
            self.reason = f"market cap ${self.market_cap/1e9:.1f}B < $10B"
            self.score = 0
            return
        
        if self.iv and (self.iv < MIN_IV or self.iv > MAX_IV):
            self.color = "🔴"
            self.reason = f"IV {self.iv:.0%} outside 35%-65% range"
            self.score = 0
            return
        
        # 🟡 YELLOW checks (right stock, wrong moment)
        yellow_flags = []
        
        if self.days_to_earnings and self.days_to_earnings < MIN_DAYS_TO_EARNINGS:
            yellow_flags.append(f"earnings in {self.days_to_earnings}d")
        
        if self.dte < 25 or self.dte > 45:
            yellow_flags.append(f"DTE {self.dte}d outside 25-45d")
        
        if self.delta < 0.20 or self.delta > 0.30:
            yellow_flags.append(f"delta {self.delta:.2f} outside 0.20-0.30")
        
        if self.cushion_pct < 0.08:
            yellow_flags.append(f"cushion {self.cushion_pct:.1%} < 8%")
        
        if self.premium_pct_of_strike < 1.0:
            yellow_flags.append(f"premium {self.premium_pct_of_strike:.2f}% < 1.0% of strike")
        
        if self.oi < 500:
            yellow_flags.append(f"OI {self.oi} < 500")
        
        if self.spread_pct > 0.05:
            yellow_flags.append(f"spread {self.spread_pct:.1%} > 5%")
        
        if yellow_flags:
            self.color = "🟡"
            self.reason = "; ".join(yellow_flags)
            self._calculate_score()  # Still calculate score for YELLOW ranking
            return
        
        # 🟢 GREEN (execute)
        self.color = "🟢"
        self.reason = "all filters passed"
        self._calculate_score()
    
    def _calculate_score(self) -> None:
        """Calculate 0-100 score based on weighted components.
        
        Components:
        - Premium quality (40%): Higher premium % of strike = better
        - Cushion % (30%): Greater distance from current = safer
        - IV level (20%): Closer to 50% midpoint is ideal
        - Liquidity (10%): Higher OI and tighter spread
        """
        
        # Premium quality (40%) - Scale to 0-100, capped at 5% = 100
        premium_score = min(100, (self.premium_pct_of_strike / 5.0) * 100)
        
        # Cushion % (30%) - Scale to 0-100, 15% = 100
        cushion_score = min(100, (self.cushion_pct / 0.15) * 100)
        
        # IV level (20%) - Closer to 50% (midpoint of 35-65) is better
        if self.iv:
            iv_center = (MIN_IV + MAX_IV) / 2  # 0.50
            iv_distance = abs(self.iv - iv_center)
            iv_max_distance = (MAX_IV - MIN_IV) / 2  # 0.15
            iv_score = max(0, 100 - (iv_distance / iv_max_distance) * 100)
        else:
            iv_score = 50  # Neutral if IV not available
        
        # Liquidity (10%) - OI and spread combined
        oi_score = min(100, (self.oi / 1000.0) * 100)  # 1000+ OI = 100
        spread_score = max(0, 100 - (self.spread_pct / 0.05) * 100)  # 5% spread = 0
        liquidity_score = (oi_score + spread_score) / 2
        
        self.score = (
            premium_score * 0.40 +
            cushion_score * 0.30 +
            iv_score * 0.20 +
            liquidity_score * 0.10
        )
    
    def format_green(self) -> str:
        """Format as GREEN candidate (detailed)."""
        iv_str = f"IV: {self.iv:.0%}" if self.iv else ""
        return (
            f"🟢 *{self.ticker}* ${self.price:.2f} | {iv_str}\n"
            f"  Strike: ${self.strike:.0f} | Delta: {self.delta:.2f} | DTE: {self.dte}d\n"
            f"  Premium: ${self.premium:.2f} ({self.premium_pct_of_strike:.2f}% of strike)\n"
            f"  Cushion: {self.cushion_pct:.1%} | Annualized: {self.annualized:.0%}\n"
            f"  Liquidity: OI={self.oi} Spread={self.spread_pct:.1%} | Score: {self.score:.1f}/100"
        )
    
    def format_yellow(self) -> str:
        """Format as YELLOW candidate (collapsed)."""
        return (
            f"🟡 *{self.ticker}*: {self.reason} | Score: {self.score:.1f}"
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
    """Organize candidates by color and rank by score.
    
    Returns:
    {
        "🟢": [green_candidates_ranked_by_score],
        "🟡": [yellow_candidates_ranked_by_score],
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
    
    # Sort by score (descending) for ranking
    for color in ["🟢", "🟡"]:
        organized[color].sort(key=lambda c: c.score, reverse=True)
    
    # RED doesn't need sorting
    
    return organized


def format_report(candidates: list[Candidate], limit_green: int = 3, limit_yellow: int = 10, hide_red: bool = True) -> str:
    """Format report with GREEN and YELLOW only (RED hidden by default)."""
    organized = score_candidates(candidates)
    
    lines = ["🚦 *Prospector Report*\n"]
    
    # 🟢 GREEN (execute now) with medals
    if organized["🟢"]:
        lines.append("*🟢 GREEN — EXECUTE NOW*")
        for i, c in enumerate(organized["🟢"][:limit_green], 1):
            medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else ""
            lines.append(f"{medal} #{i} {c.format()}")
        lines.append("")
    else:
        lines.append("*🟢 GREEN — None found*\n")
    
    # 🟡 YELLOW (right stock, wrong moment)
    if organized["🟡"]:
        lines.append("*🟡 YELLOW — RIGHT STOCK, WRONG MOMENT*")
        for c in organized["🟡"][:limit_yellow]:
            lines.append(c.format())
        lines.append("")
    else:
        lines.append("*🟡 YELLOW — None*\n")
    
    # 🔴 RED (hidden by default - too noisy)
    if not hide_red and organized["🔴"]:
        lines.append("*🔴 RED — SKIP (FUNDAMENTAL ISSUES)*")
        for c in organized["🔴"][:5]:
            lines.append(c.format())
        if len(organized["🔴"]) > 5:
            lines.append(f"  ... and {len(organized['🔴']) - 5} more")
    
    return "\n".join(lines)
