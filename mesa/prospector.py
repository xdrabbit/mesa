"""Prospector agent — scans S&P 500 + Nasdaq 100 for covered call opportunities.

Pipeline:
  1. Load priority watchlist (20 hand-picked tickers, marked ⭐ in output).
  2. Load full universe (S&P 500 ∪ Nasdaq 100) from cached Wikipedia data.
  3. Pre-filter via yfinance .info: price band, market cap, sector, country, earnings.
  4. Score remaining option chains: IV, DTE, delta, premium, liquidity, cushion.
  5. Emit top 5 picks via Telegram. If zero pass, surface top 3 nearest misses.

Open positions are loaded live from positions.json (mesa.models.load_positions),
so closing or opening a position is reflected on the next scan.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta

import yfinance as yf

from mesa.models import load_positions
from mesa.telegram_send import send
from mesa.universe import load_universe

log = logging.getLogger(__name__)

# Priority watchlist — quality stocks, scanned first, marked ⭐.
WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
    "JPM", "V", "MA", "UNH", "JNJ", "PG", "KO",
    "WMT", "HD", "ABBV", "DDOG", "CRM", "NOW",
]

# --- Pre-filter (cheap pass, .info only) ---
PREFILTER_MIN_PRICE = 10.0
PREFILTER_MAX_PRICE = 200.0
PREFILTER_MIN_MARKET_CAP = 1e10  # $10B
PREFILTER_MIN_DAYS_TO_EARNINGS = 14

TICKER_SUBSTRING_EXCLUSIONS = ("MARA", "RIOT", "MSTR", "COIN", "SQQQ", "TQQQ", "UVXY")

EXCLUDED_INDUSTRY_KEYWORDS = (
    "biotechnology",
    "pharmaceutical",
    "drug manufacturers",
    "crypto",
)

# --- Scoring (full options pull) ---
SCAN_MIN_PRICE = 70.0
SCAN_MAX_PRICE = 120.0  # ~$10K per-position capital limit (100 shares × $120 ≈ $12K wheel ceiling)
MIN_IV = 0.35
MAX_IV = 0.65
TARGET_DTE_MIN = 25
TARGET_DTE_MAX = 45
TARGET_DELTA_MIN = 0.20
TARGET_DELTA_MAX = 0.30
MIN_PREMIUM_PCT_OF_STRIKE = 0.01
MIN_OPEN_INTEREST = 500
MAX_BID_ASK_SPREAD_PCT = 0.05

# Tier thresholds: priority watchlist gets relaxed credit/cushion, others stricter.
CORE_MIN_CREDIT = 2.00
CORE_MIN_CUSHION = 0.08
NON_CORE_MIN_CREDIT = 2.50
NON_CORE_MIN_CUSHION = 0.08

MAX_WORKERS = 10
TOP_N = 5
NEAREST_MISSES = 3


@dataclass
class Candidate:
    ticker: str
    is_priority: bool
    price: float
    iv: float
    strike: float
    premium: float
    dte: int
    annualized: float

    @property
    def score(self) -> float:
        return self.annualized

    def format_line(self, idx: int) -> str:
        marker = "⭐ " if self.is_priority else ""
        return (
            f"{idx}. {marker}{self.ticker} ${self.price:.0f} | "
            f"IV {self.iv * 100:.0f}% | ${self.strike:.0f}p | "
            f"${self.premium:.2f} premium | {self.annualized * 100:.0f}% ann."
        )


@dataclass
class NearMiss:
    ticker: str
    is_priority: bool
    reason: str

    def format_line(self, idx: int) -> str:
        marker = "⭐ " if self.is_priority else ""
        return f"{idx}. {marker}{self.ticker} — {self.reason}"


def _open_position_tickers() -> set[str]:
    return {p.ticker for p in load_positions() if not p.closed}


def _passes_substring_exclusion(ticker: str) -> bool:
    return not any(sub in ticker for sub in TICKER_SUBSTRING_EXCLUSIONS)


def _passes_sector_filter(info: dict) -> tuple[bool, str]:
    sector = (info.get("sector") or "").strip()
    industry = (info.get("industry") or "").strip().lower()

    for kw in EXCLUDED_INDUSTRY_KEYWORDS:
        if kw in industry:
            return False, f"industry: {info.get('industry')}"

    # Financial Services excluded except REITs. yfinance usually puts REITs in
    # 'Real Estate' sector — this clause is defensive against alternate categorizations.
    if sector == "Financial Services" and "reit" not in industry:
        return False, "Financial Services (non-REIT)"

    return True, ""


def _next_earnings_within(t: yf.Ticker, days: int) -> bool | None:
    """True if next earnings is within `days`; False if not; None if unknown."""
    try:
        cal = t.calendar
    except Exception:
        return None
    if not cal:
        return None
    earnings_dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
    if not earnings_dates:
        return None
    today = date.today()
    cutoff = today + timedelta(days=days)
    for ed in earnings_dates:
        try:
            ed_date = ed.date() if hasattr(ed, "date") else date.fromisoformat(str(ed)[:10])
        except (ValueError, TypeError):
            continue
        if today <= ed_date <= cutoff:
            return True
    return False


def _prefilter(ticker: str) -> tuple[yf.Ticker | None, dict | None, str | None]:
    """Return (ticker_obj, info, None) on pass, or (None, None, reason) on fail."""
    if not _passes_substring_exclusion(ticker):
        return None, None, "leveraged/crypto/volatility ticker"

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
    except Exception as e:
        return None, None, f"yfinance error: {e}"

    country = info.get("country")
    if country and country != "United States":
        return None, None, f"non-US ({country})"

    market_cap = info.get("marketCap")
    if not market_cap:
        return None, None, "no market cap data"
    if market_cap < PREFILTER_MIN_MARKET_CAP:
        return None, None, f"cap ${market_cap / 1e9:.1f}B < $10B"

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if not price:
        return None, None, "no price data"
    if price < PREFILTER_MIN_PRICE or price > PREFILTER_MAX_PRICE:
        return None, None, f"price ${price:.2f} outside ${PREFILTER_MIN_PRICE:.0f}-${PREFILTER_MAX_PRICE:.0f}"

    sector_ok, sector_reason = _passes_sector_filter(info)
    if not sector_ok:
        return None, None, sector_reason

    if _next_earnings_within(t, PREFILTER_MIN_DAYS_TO_EARNINGS) is True:
        return None, None, f"earnings within {PREFILTER_MIN_DAYS_TO_EARNINGS}d"

    return t, info, None


def _score_options(
    t: yf.Ticker, info: dict, ticker: str, is_priority: bool, today: date
) -> tuple[list[Candidate], NearMiss | None]:
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if not (SCAN_MIN_PRICE <= price <= SCAN_MAX_PRICE):
        return [], NearMiss(
            ticker, is_priority,
            f"price ${price:.0f} outside scan range ${SCAN_MIN_PRICE:.0f}-${SCAN_MAX_PRICE:.0f}",
        )

    tier_credit = CORE_MIN_CREDIT if is_priority else NON_CORE_MIN_CREDIT
    tier_cushion = CORE_MIN_CUSHION if is_priority else NON_CORE_MIN_CUSHION

    candidates: list[Candidate] = []
    best_miss: NearMiss | None = None

    try:
        expiries = t.options
    except Exception as e:
        return [], NearMiss(ticker, is_priority, f"no option chain ({e})")

    for exp_str in expiries:
        try:
            exp_date = date.fromisoformat(exp_str)
        except ValueError:
            continue
        dte = (exp_date - today).days
        if not (TARGET_DTE_MIN <= dte <= TARGET_DTE_MAX):
            continue

        try:
            chain = t.option_chain(exp_str)
        except Exception:
            continue

        for _, row in chain.calls.iterrows():
            try:
                strike = float(row.get("strike", 0))
            except (ValueError, TypeError):
                continue
            if strike <= price:
                continue

            cushion = (strike - price) / price
            if cushion < tier_cushion:
                continue

            try:
                iv = float(row.get("impliedVolatility", 0) or 0)
            except (ValueError, TypeError):
                iv = 0.0
            if not (MIN_IV <= iv <= MAX_IV):
                if best_miss is None:
                    best_miss = NearMiss(
                        ticker, is_priority,
                        f"IV {iv * 100:.0f}% outside {int(MIN_IV * 100)}-{int(MAX_IV * 100)}%",
                    )
                continue

            # Delta is optional — many yfinance chains don't include it.
            delta_raw = row.get("delta")
            if delta_raw is not None and delta_raw == delta_raw:  # NaN-safe
                try:
                    delta = float(delta_raw)
                    if not (TARGET_DELTA_MIN <= delta <= TARGET_DELTA_MAX):
                        continue
                except (ValueError, TypeError):
                    pass

            try:
                bid = float(row.get("bid", 0) or 0)
                ask = float(row.get("ask", 0) or 0)
                oi = int(row.get("openInterest", 0) or 0)
            except (ValueError, TypeError):
                continue
            if bid <= 0 or ask <= 0:
                continue
            if oi < MIN_OPEN_INTEREST:
                continue
            spread_pct = (ask - bid) / bid
            if spread_pct > MAX_BID_ASK_SPREAD_PCT:
                continue

            mid = (bid + ask) / 2
            if mid < tier_credit:
                if best_miss is None:
                    gap = (tier_credit - mid) * 100
                    best_miss = NearMiss(ticker, is_priority, f"credit ${mid:.2f} short by ${gap:.0f}")
                continue

            if (mid / strike) < MIN_PREMIUM_PCT_OF_STRIKE:
                continue

            annualized = (mid / price) * (365 / dte) if dte > 0 else 0.0

            candidates.append(Candidate(
                ticker=ticker,
                is_priority=is_priority,
                price=price,
                iv=iv,
                strike=strike,
                premium=mid,
                dte=dte,
                annualized=annualized,
            ))

    return candidates, best_miss


def _scan_one(ticker: str, is_priority: bool, today: date) -> tuple[list[Candidate], NearMiss | None]:
    t, info, reject = _prefilter(ticker)
    if reject:
        log.debug("%s rejected: %s", ticker, reject)
        return [], None
    return _score_options(t, info, ticker, is_priority, today)


def run() -> None:
    today = date.today()
    open_positions = _open_position_tickers()
    log.info("Excluding %d open positions: %s", len(open_positions), sorted(open_positions))

    universe = load_universe()
    priority_set = set(WATCHLIST)
    universe_only = [t for t in universe if t not in priority_set]

    targets: list[tuple[str, bool]] = []
    targets.extend((t, True) for t in WATCHLIST if t not in open_positions)
    targets.extend((t, False) for t in universe_only if t not in open_positions)

    log.info(
        "Scanning %d tickers (%d priority + %d universe)",
        len(targets),
        sum(1 for _, p in targets if p),
        sum(1 for _, p in targets if not p),
    )

    all_candidates: list[Candidate] = []
    all_misses: list[NearMiss] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_scan_one, t, p, today): t for t, p in targets}
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                hits, miss = fut.result()
                all_candidates.extend(hits)
                if miss:
                    all_misses.append(miss)
            except Exception as e:
                log.warning("Scan failed for %s: %s", ticker, e)

    # Priority hits float to top, then sort by annualized return.
    all_candidates.sort(key=lambda c: (not c.is_priority, -c.score))
    top_picks = all_candidates[:TOP_N]

    if top_picks:
        lines = [f"🟢 *TOP PICKS* — {today}", ""]
        for i, c in enumerate(top_picks, 1):
            lines.append(c.format_line(i))
        send("\n".join(lines))
        return

    log.info("No qualifying picks; surfacing nearest misses")
    all_misses.sort(key=lambda m: not m.is_priority)
    misses = all_misses[:NEAREST_MISSES]

    lines = [f"🔭 *PROSPECTOR* — {today}", "", "_No qualifying picks today._", ""]
    if misses:
        lines.append("*Nearest misses:*")
        for i, m in enumerate(misses, 1):
            lines.append(m.format_line(i))
    else:
        lines.append("_All scanned tickers filtered out before scoring._")
    send("\n".join(lines))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
