"""Prospector agent — scans S&P 500 + Nasdaq 100 for cash-secured put opportunities.

Pipeline:
  1. Load priority watchlist (20 hand-picked tickers, marked ⭐ in output).
  2. Load full universe (S&P 500 ∪ Nasdaq 100) from cached Wikipedia data.
  3. Pre-filter via yfinance .info: price band ($50-$110), market cap, sector, country,
     solvency/quality.
  4. Score remaining put chains: per-expiration earnings exclusion (fail-closed), IV, DTE,
     delta, premium, liquidity, cushion.
  5. Rank by the risk-adjusted mesa.scoring blend (not raw yield); priority names float to top.
  6. Emit top 5 picks via Telegram. If zero pass, surface top 3 nearest misses.

Open positions are loaded live from positions.json (mesa.models.load_positions),
so closing or opening a position is reflected on the next scan.

The engine is shared: `scan()` + `format_report()` back both the cron job (`run()`) and the
Telegram bot (mesa.conversational), so every gate below applies to both.
"""
from __future__ import annotations

import logging
import math
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from typing import NamedTuple

import yfinance as yf

from mesa.models import load_positions
from mesa.scoring import ACCOUNTING_ISSUES, risk_score
from mesa.telegram_send import send
from mesa.universe import load_shortlist, load_universe, save_shortlist

log = logging.getLogger(__name__)

# Priority watchlist — quality stocks, scanned first, marked ⭐.
WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
    "JPM", "V", "MA", "UNH", "JNJ", "PG", "KO",
    "WMT", "HD", "ABBV", "DDOG", "CRM", "NOW",
]

# --- Pre-filter (cheap pass, .info only) ---
PREFILTER_MIN_MARKET_CAP = 1e10  # $10B

TICKER_SUBSTRING_EXCLUSIONS = ("MARA", "RIOT", "MSTR", "COIN", "SQQQ", "TQQQ", "UVXY")

# --- Fix 2: solvency / quality gate (.info only) ---
# Manually-flagged credit-distress names. yfinance exposes no clean "missed/deferred debt
# interest payment" field, so distressed credits identified out-of-band (e.g. SATS per
# RECON_PROSPECTOR.md) are hard-listed here. Mirrors scoring.ACCOUNTING_ISSUES, which is also
# honored below.
SOLVENCY_DISTRESS = {
    "SATS",  # EchoStar — distressed credit / debt-service concerns (RECON_PROSPECTOR.md §6).
}
# Reject names whose trailing profit margin is deeply negative — a sourceable distress proxy.
# Fail-OPEN on a missing margin field: a >$10B name without the field is not auto-excluded,
# since healthy companies sometimes lack it. The hard solvency gate is the manual sets above.
MIN_PROFIT_MARGIN = -0.20

EXCLUDED_INDUSTRY_KEYWORDS = (
    "biotechnology",
    "pharmaceutical",
    "drug manufacturers",
    "crypto",
)

# --- Scoring (full options pull) ---
SCAN_MIN_PRICE = 50.0
SCAN_MAX_PRICE = 110.0  # Wheel strategy capital range from SCREENING_STRATEGY.md.
MIN_IV = 0.0
MAX_IV = 1.50
TARGET_DTE_MIN = 30
TARGET_DTE_MAX = 60
TARGET_DELTA_MIN = 0.20
TARGET_DELTA_MAX = 0.35
MIN_PREMIUM_PCT_OF_STRIKE = 0.01
MIN_OPEN_INTEREST = 100
MAX_BID_ASK_SPREAD_PCT = 0.20

# Tier thresholds: priority watchlist gets relaxed credit/cushion, others stricter.
CORE_MIN_CREDIT = 0.0
CORE_MIN_CUSHION = 0.05
NON_CORE_MIN_CREDIT = 0.0
NON_CORE_MIN_CUSHION = 0.05

NO_QUOTE_REASON = "no live bid/ask on any strike in range (market closed?)"
DATA_ERROR_CHAIN = "option chain unavailable (rate-limited?)"
# Reasons that mean "Yahoo failed us", not "the ticker failed the screen" — tallied so a throttled
# scan says so instead of quietly returning fewer picks.
_DATA_ERROR_PREFIXES = ("yfinance error", "yfinance returned incomplete", "no option chain", DATA_ERROR_CHAIN)
DATA_ERROR_NOTE_MIN = 3  # ...and at least this share (below) of the scan, so one flaky ticker is silent
DATA_ERROR_NOTE_SHARE = 0.03
RISK_FREE_RATE = 0.04  # for the Black-Scholes delta estimate; yfinance chains carry no greeks.

MAX_WORKERS = 10
TOP_N = 5
NEAREST_MISSES = 3


@dataclass(frozen=True)
class ScanParams:
    """Per-request knobs. Defaults reproduce the cron job's behavior exactly."""

    price_min: float = SCAN_MIN_PRICE
    price_max: float = SCAN_MAX_PRICE
    min_iv: float = MIN_IV


DEFAULT_PARAMS = ScanParams()


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
    # Risk-score inputs (Fix 3). Annualized yield is preserved above as a displayed metric and
    # as the sort tiebreaker; it is no longer the sole ranking key.
    cushion_pct: float = 0.0
    oi: int = 0
    spread_pct: float = 0.0
    premium_pct_of_strike: float = 0.0  # PERCENT of strike (e.g. 1.5 == 1.5%).
    expiry: str = ""  # YYYY-MM-DD
    delta: float = 0.0  # |delta| (estimated from IV when the chain has none)

    @property
    def score(self) -> float:
        """Risk-adjusted 0-100 score (Fix 3) via the shared mesa.scoring.risk_score blend."""
        return risk_score(
            premium_pct_of_strike=self.premium_pct_of_strike,
            cushion_pct=self.cushion_pct,
            iv=self.iv,
            oi=self.oi,
            spread_pct=self.spread_pct,
        )

    def format_line(self, idx: int) -> str:
        marker = "⭐ " if self.is_priority else ""
        when = f"{self.expiry} " if self.expiry else ""
        delta = f", Δ{self.delta:.2f}" if self.delta else ""
        return (
            f"{idx}. {marker}{self.ticker} ${self.price:.2f} — sell {when}${self.strike:g} put "
            f"({self.dte}d{delta})\n"
            f"   ${self.premium:.2f} credit · {self.premium_pct_of_strike:.1f}% of strike · "
            f"{self.annualized * 100:.0f}% ann. · cushion {self.cushion_pct * 100:.0f}% · "
            f"IV {self.iv * 100:.0f}% · OI {self.oi:,} · score {self.score:.0f}/100"
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


def _next_earnings_date(t: yf.Ticker, today: date) -> date | None:
    """Soonest earnings date on/after `today`, or None if unavailable/unparseable.

    `today` is the scan date (not the wall clock) so scans and tests stay deterministic.

    Returns None whenever the data source is blind (no calendar, exception, only past dates).
    Callers treat None as 'unknown' and FAIL CLOSED (exclude) — see `_earnings_admits`.
    """
    try:
        cal = t.calendar
    except Exception:
        return None
    if not cal:
        return None
    earnings_dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
    if not earnings_dates:
        return None
    upcoming: list[date] = []
    for ed in earnings_dates:
        try:
            ed_date = ed.date() if hasattr(ed, "date") else date.fromisoformat(str(ed)[:10])
        except (ValueError, TypeError):
            continue
        if ed_date >= today:
            upcoming.append(ed_date)
    return min(upcoming) if upcoming else None


def _earnings_admits(earnings_date: date | None, exp_date: date) -> tuple[bool, str]:
    """Fix 1: earnings must not land within the option's life, and FAIL CLOSED if unknown.

    Excludes when the next earnings date is unknown (treated as unsafe) or falls on/before the
    expiration being evaluated — using the actual expiration, not a fixed look-ahead window.
    """
    if earnings_date is None:
        return False, "earnings-unknown-excluded"
    if earnings_date <= exp_date:
        return False, f"earnings {earnings_date} on/before exp {exp_date}"
    return True, ""


def _solvency_admits(info: dict, ticker: str) -> tuple[bool, str]:
    """Fix 2: fundamentals/solvency gate. Returns (admit, reason).

    Hard exclusions: manual distressed-credit list and scoring.ACCOUNTING_ISSUES. Sourceable
    proxy: deeply negative trailing profit margin. Fail-OPEN on a missing margin field (see
    MIN_PROFIT_MARGIN note) — the manual sets are the hard gate, the margin is a best-effort add.
    """
    if ticker in ACCOUNTING_ISSUES:
        return False, "solvency-excluded: accounting/SEC watchlist"
    if ticker in SOLVENCY_DISTRESS:
        return False, "solvency-excluded: distressed-credit watchlist"

    pm = info.get("profitMargins")
    try:
        if pm is not None and float(pm) < MIN_PROFIT_MARGIN:
            return False, f"solvency-excluded: profit margin {float(pm):.0%} < {MIN_PROFIT_MARGIN:.0%}"
    except (ValueError, TypeError):
        pass

    return True, ""


def _warm_up() -> None:
    """Prime yfinance's cookie/crumb with one request before fanning out to threads.

    Ten threads all asking for a crumb at once trips Yahoo's rate limit ("429" / "Invalid Crumb").
    """
    try:
        yf.Ticker("SPY").info
    except Exception as e:  # best effort — scans still work, just noisier
        log.debug("yfinance warm-up failed: %s", e)


def _info_is_stub(info: dict) -> bool:
    return not (info.get("marketCap") or info.get("currentPrice") or info.get("regularMarketPrice"))


def _fetch_info(ticker: str, attempts: int = 4) -> tuple[yf.Ticker, dict]:
    """(Ticker, .info) with retries.

    Under parallel load Yahoo answers ~1 in 3 `.info` calls with a one-key stub
    (`{'trailingPegRatio': None}`) instead of an error, which would silently drop the ticker as
    "no market cap data". Treat a stub like an error: back off and retry on a *fresh* Ticker,
    because yfinance can cache the stub on the instance.
    """
    last_exc: Exception | None = None
    stub: tuple[yf.Ticker, dict] | None = None
    for i in range(attempts):
        t = yf.Ticker(ticker)
        try:
            info = t.info or {}
        except Exception as e:
            last_exc = e
        else:
            if not _info_is_stub(info):
                return t, info
            stub, last_exc = (t, info), None
        if i < attempts - 1:
            time.sleep(0.75 * (i + 1))
    if last_exc is not None:
        raise last_exc
    return stub  # type: ignore[return-value]


def _prefilter(
    ticker: str, params: ScanParams = DEFAULT_PARAMS
) -> tuple[yf.Ticker | None, dict | None, str | None]:
    """Return (ticker_obj, info, None) on pass, or (None, None, reason) on fail."""
    if not _passes_substring_exclusion(ticker):
        return None, None, "leveraged/crypto/volatility ticker"

    try:
        t, info = _fetch_info(ticker)
    except Exception as e:
        return None, None, f"yfinance error: {e}"

    country = info.get("country")
    if country and country != "United States":
        return None, None, f"non-US ({country})"

    market_cap = info.get("marketCap")
    if not market_cap:
        if _info_is_stub(info):
            return None, None, "yfinance returned incomplete data (rate-limited?)"
        return None, None, "no market cap data"
    if market_cap < PREFILTER_MIN_MARKET_CAP:
        return None, None, f"cap ${market_cap / 1e9:.1f}B < $10B"

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if not price:
        return None, None, "no price data"
    if price < params.price_min or price > params.price_max:
        return None, None, f"price ${price:.2f} outside ${params.price_min:.0f}-${params.price_max:.0f}"

    sector_ok, sector_reason = _passes_sector_filter(info)
    if not sector_ok:
        return None, None, sector_reason

    solvency_ok, solvency_reason = _solvency_admits(info, ticker)
    if not solvency_ok:
        return None, None, solvency_reason

    # NOTE: earnings proximity is no longer gated here on a fixed 14-day window. It is enforced
    # per-expiration in `_score_options` (Fix 1), comparing the next earnings date to the actual
    # option expiration and failing closed when earnings data is unavailable.
    return t, info, None


def _put_delta(price: float, strike: float, dte: int, iv: float, rate: float = RISK_FREE_RATE) -> float | None:
    """|delta| of a put via Black-Scholes, or None if inputs make it undefined.

    yfinance option chains carry no greeks, so the delta band would otherwise never apply.
    N(-d1) == 0.5 * erfc(d1 / sqrt(2)).
    """
    if price <= 0 or strike <= 0 or iv <= 0 or dte <= 0:
        return None
    t = dte / 365.0
    d1 = (math.log(price / strike) + (rate + iv * iv / 2.0) * t) / (iv * math.sqrt(t))
    return 0.5 * math.erfc(d1 / math.sqrt(2.0))


def _score_options(
    t: yf.Ticker,
    info: dict,
    ticker: str,
    is_priority: bool,
    today: date,
    params: ScanParams = DEFAULT_PARAMS,
) -> tuple[list[Candidate], NearMiss | None]:
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if not price:
        return [], NearMiss(ticker, is_priority, "no price data")
    if not (params.price_min <= price <= params.price_max):
        return [], NearMiss(
            ticker, is_priority,
            f"price ${price:.0f} outside scan range ${params.price_min:.0f}-${params.price_max:.0f}",
        )

    tier_credit = CORE_MIN_CREDIT if is_priority else NON_CORE_MIN_CREDIT
    tier_cushion = CORE_MIN_CUSHION if is_priority else NON_CORE_MIN_CUSHION

    candidates: list[Candidate] = []
    # Why rows were rejected, tallied so the near-miss names the *dominant* blocker rather than
    # whichever row happened to come first.
    row_misses: Counter[str] = Counter()
    live_rows = dead_rows = 0  # rows with / without a bid+ask, among strikes that passed the cushion test
    chain_errors = 0
    earnings_miss: str | None = None
    expiries_in_window = 0

    # Fix 1: resolve the next earnings date once. None ⇒ unknown ⇒ every expiration fails closed.
    earnings_date = _next_earnings_date(t, today)

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
        expiries_in_window += 1

        # Fix 1: exclude this expiration if earnings land within its life (or are unknown).
        earnings_ok, earnings_reason = _earnings_admits(earnings_date, exp_date)
        if not earnings_ok:
            earnings_miss = earnings_miss or earnings_reason
            continue

        try:
            chain = t.option_chain(exp_str)
        except Exception as e:
            log.debug("%s %s: option chain fetch failed: %s", ticker, exp_str, e)
            chain_errors += 1
            continue

        for _, row in chain.puts.iterrows():
            try:
                strike = float(row.get("strike", 0))
            except (ValueError, TypeError):
                continue
            if strike >= price:
                continue

            cushion = (price - strike) / price
            if cushion < tier_cushion:
                continue

            # Live quote first. Outside market hours Yahoo returns bid=ask=0 and junk IV, so
            # everything below would be judged on garbage (and blamed on the wrong filter).
            try:
                bid = float(row.get("bid", 0) or 0)
                ask = float(row.get("ask", 0) or 0)
                oi = int(row.get("openInterest", 0) or 0)
            except (ValueError, TypeError):
                continue
            if bid <= 0 or ask <= 0:
                # Deep-OTM strikes routinely have no bid even in an open market, so a dead row is
                # only "market closed" if *every* row is dead (decided after the loop).
                dead_rows += 1
                continue
            live_rows += 1

            try:
                iv = float(row.get("impliedVolatility", 0) or 0)
            except (ValueError, TypeError):
                iv = 0.0
            iv_floor = max(MIN_IV, params.min_iv)
            if not (iv_floor <= iv <= MAX_IV):
                row_misses[f"IV outside {int(iv_floor * 100)}-{int(MAX_IV * 100)}%"] += 1
                continue

            # Delta: use the chain's if present (rare for yfinance), else estimate from IV.
            delta: float | None = None
            delta_raw = row.get("delta")
            if delta_raw is not None and delta_raw == delta_raw:  # NaN-safe
                try:
                    delta = abs(float(delta_raw))
                except (ValueError, TypeError):
                    delta = None
            if delta is None:
                delta = _put_delta(price, strike, dte, iv)
            if delta is None:  # no usable IV ⇒ can't judge the risk ⇒ skip the row
                row_misses["no usable IV"] += 1
                continue
            if not (TARGET_DELTA_MIN <= delta <= TARGET_DELTA_MAX):
                row_misses[f"no strike in the {TARGET_DELTA_MIN:.2f}-{TARGET_DELTA_MAX:.2f} delta band"] += 1
                continue

            if oi < MIN_OPEN_INTEREST:
                row_misses[f"open interest under {MIN_OPEN_INTEREST}"] += 1
                continue
            spread_pct = (ask - bid) / bid
            if spread_pct > MAX_BID_ASK_SPREAD_PCT:
                row_misses[f"bid-ask spread over {int(MAX_BID_ASK_SPREAD_PCT * 100)}%"] += 1
                continue

            mid = (bid + ask) / 2
            if mid < tier_credit:
                row_misses[f"credit under ${tier_credit:.2f}"] += 1
                continue

            if (mid / strike) < MIN_PREMIUM_PCT_OF_STRIKE:
                row_misses[f"premium under {MIN_PREMIUM_PCT_OF_STRIKE * 100:.0f}% of strike"] += 1
                continue

            annualized = (mid / strike) * (365 / dte) if dte > 0 else 0.0

            candidates.append(Candidate(
                ticker=ticker,
                is_priority=is_priority,
                price=price,
                iv=iv,
                strike=strike,
                premium=mid,
                dte=dte,
                annualized=annualized,
                cushion_pct=cushion,
                oi=oi,
                spread_pct=spread_pct,
                premium_pct_of_strike=(mid / strike) * 100,
                expiry=exp_str,
                delta=delta,
            ))

    if candidates:
        return candidates, None
    if row_misses:
        return [], NearMiss(ticker, is_priority, row_misses.most_common(1)[0][0])
    if dead_rows and not live_rows:
        return [], NearMiss(ticker, is_priority, NO_QUOTE_REASON)
    if chain_errors and not live_rows:
        return [], NearMiss(ticker, is_priority, DATA_ERROR_CHAIN)
    if earnings_miss:
        return [], NearMiss(ticker, is_priority, earnings_miss)
    if expiries_in_window == 0:
        return [], NearMiss(ticker, is_priority, f"no expiry in the {TARGET_DTE_MIN}-{TARGET_DTE_MAX} DTE window")
    return [], None


class _Outcome(NamedTuple):
    candidates: list[Candidate]
    miss: NearMiss | None
    passed_prefilter: bool
    data_error: bool = False  # Yahoo failed (rate limit / stub / chain error) — not a screening result


def _scan_one(
    ticker: str,
    is_priority: bool,
    today: date,
    params: ScanParams = DEFAULT_PARAMS,
    explain: bool = False,
) -> _Outcome:
    """Prefilter + score one ticker. With `explain`, a prefilter rejection is returned as a
    near-miss (used when the user asked about a specific ticker and deserves to know why not)."""
    t, info, reject = _prefilter(ticker, params)
    if reject:
        log.debug("%s rejected: %s", ticker, reject)
        return _Outcome(
            [], NearMiss(ticker, is_priority, reject) if explain else None, False,
            data_error=reject.startswith(_DATA_ERROR_PREFIXES),
        )
    cands, miss = _score_options(t, info, ticker, is_priority, today, params)
    return _Outcome(cands, miss, True, data_error=bool(miss and miss.reason.startswith(_DATA_ERROR_PREFIXES)))


@dataclass
class ScanResult:
    candidates: list[Candidate]  # best put per ticker, ranked
    misses: list[NearMiss]
    scanned: int
    prefiltered: list[str]  # tickers that passed the cheap prefilter (→ bot shortlist)
    notes: list[str] = field(default_factory=list)
    data_errors: list[str] = field(default_factory=list)  # tickers Yahoo failed to serve


def _rank_key(c: Candidate) -> tuple:
    # Priority hits still float to top, then the risk-adjusted score, then yield as tiebreaker.
    return (not c.is_priority, -c.score, -c.annualized)


def scan(
    targets: list[tuple[str, bool]],
    params: ScanParams = DEFAULT_PARAMS,
    today: date | None = None,
    explain: bool = False,
    workers: int = MAX_WORKERS,
) -> ScanResult:
    """Scan (ticker, is_priority) targets in parallel. One result per ticker, best pick first."""
    today = today or date.today()
    if targets:
        _warm_up()

    all_candidates: list[Candidate] = []
    all_misses: list[NearMiss] = []
    prefiltered: list[str] = []
    data_errors: list[str] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_scan_one, t, p, today, params, explain): t for t, p in targets}
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                outcome = fut.result()
            except Exception as e:
                log.warning("Scan failed for %s: %s", ticker, e)
                if explain:
                    all_misses.append(NearMiss(ticker, False, f"scan error: {e}"))
                continue
            all_candidates.extend(outcome.candidates)
            if outcome.data_error:
                data_errors.append(ticker)
            if outcome.miss:
                all_misses.append(outcome.miss)
            if outcome.passed_prefilter:
                prefiltered.append(ticker)

    best: dict[str, Candidate] = {}
    for c in all_candidates:
        if c.ticker not in best or _rank_key(c) < _rank_key(best[c.ticker]):
            best[c.ticker] = c
    ranked = sorted(best.values(), key=_rank_key)
    all_misses.sort(key=lambda m: (not m.is_priority, m.ticker))

    result = ScanResult(ranked, all_misses, len(targets), sorted(prefiltered), data_errors=sorted(data_errors))
    if len(data_errors) >= max(DATA_ERROR_NOTE_MIN, DATA_ERROR_NOTE_SHARE * len(targets)):
        result.notes.append(
            f"⚠️ Yahoo failed to return data for {len(data_errors)} of {len(targets)} tickers "
            "(rate limit?) — results may be incomplete. Try again in a few minutes."
        )
    # A real pick needs a live bid/ask, so the market is evidently open if any exist.
    dead_quotes = sum(1 for m in all_misses if m.reason == NO_QUOTE_REASON)
    if not ranked and dead_quotes and dead_quotes >= max(1, len(prefiltered) // 2):
        result.notes.append(
            "⚠️ Options quotes are empty — the market is probably closed, so results are "
            "unreliable. Scan Mon–Fri 9:30–16:00 ET."
        )
    return result


def format_report(
    result: ScanResult,
    today: date,
    limit: int = TOP_N,
    max_misses: int = NEAREST_MISSES,
    explain: bool = False,
) -> str:
    """Render a scan. With `explain` (user asked about specific tickers) the near-misses are
    listed even when some tickers did qualify, so every ticker asked about gets an answer."""
    notes = [f"_{n}_" for n in result.notes]
    if result.candidates:
        lines = [f"🟢 *TOP PICKS* — {today} ({result.scanned} scanned)", ""]
        for i, c in enumerate(result.candidates[:limit], 1):
            lines.append(c.format_line(i))
        if explain and result.misses:
            lines.extend(["", "*Not qualifying:*"])
            for i, m in enumerate(result.misses[:max_misses], 1):
                lines.append(m.format_line(i))
        if notes:
            lines.extend(["", *notes])
        return "\n".join(lines)

    lines = [f"🔭 *PROSPECTOR* — {today} ({result.scanned} scanned)", "", "_No qualifying picks._", ""]
    misses = result.misses[:max_misses]
    if misses:
        lines.append("*Nearest misses:*")
        for i, m in enumerate(misses, 1):
            lines.append(m.format_line(i))
    else:
        lines.append("_All scanned tickers filtered out before scoring._")
    if notes:
        lines.extend(["", *notes])
    return "\n".join(lines)


def build_targets(universe: bool = True) -> tuple[list[tuple[str, bool]], list[str]]:
    """(ticker, is_priority) pairs minus open positions, plus human-readable notes.

    Never raises: if the index universe can't be loaded the scan degrades to the priority
    watchlist and says so, instead of killing the cron job.
    """
    open_positions = _open_position_tickers()
    log.info("Excluding %d open positions: %s", len(open_positions), sorted(open_positions))

    notes: list[str] = []
    targets: list[tuple[str, bool]] = [(t, True) for t in WATCHLIST if t not in open_positions]

    if universe:
        try:
            priority_set = set(WATCHLIST)
            others = [t for t in load_universe() if t not in priority_set and t not in open_positions]
            targets.extend((t, False) for t in others)
        except Exception as e:
            log.error("Universe unavailable: %s", e)
            notes.append("Index universe unavailable — scanned the priority watchlist only.")

    return targets, notes


def default_targets(universe: bool = False) -> tuple[list[tuple[str, bool]], list[str]]:
    """Targets for a request that names no tickers: the full index list when `universe`, else the
    priority watchlist plus the shortlist saved by the last full scan (fast)."""
    if universe:
        return build_targets(universe=True)

    open_positions = _open_position_tickers()
    targets: list[tuple[str, bool]] = [(t, True) for t in WATCHLIST if t not in open_positions]
    seen = {t for t, _ in targets}
    notes: list[str] = []

    shortlist = load_shortlist()
    for t in shortlist:
        if t not in seen and t not in open_positions:
            targets.append((t, False))
            seen.add(t)
    if not shortlist:
        notes.append("No shortlist yet — send `scan all` once to build it from the full index list.")
    return targets, notes


def run() -> None:
    today = date.today()
    targets, notes = build_targets(universe=True)
    log.info(
        "Scanning %d tickers (%d priority + %d universe)",
        len(targets),
        sum(1 for _, p in targets if p),
        sum(1 for _, p in targets if not p),
    )

    result = scan(targets, today=today)
    result.notes.extend(notes)
    if result.prefiltered:
        try:
            save_shortlist(result.prefiltered)
        except OSError as e:
            log.warning("Could not save shortlist: %s", e)

    if not result.candidates:
        log.info("No qualifying picks; surfacing nearest misses")
    send(format_report(result, today))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
