"""Natural-language front end for the prospector (Telegram bot and `mesa screen`).

Parses free text ("top 5 under $80, high IV", "check ddog nflx", "scan all") into scan
parameters, runs the shared engine in `mesa.prospector` — so the earnings / solvency / delta
gates apply here exactly as they do in the cron job — and returns the reply text.

Text in, text out: the caller (Telegram handler, CLI) decides how to deliver it.
"""
from __future__ import annotations

import logging
import re
from datetime import date

from mesa.prospector import (
    SCAN_MAX_PRICE,
    SCAN_MIN_PRICE,
    WATCHLIST,
    ScanParams,
    default_targets,
    format_report,
    scan,
)
from mesa.universe import save_shortlist

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 3
MAX_LIMIT = 10
MAX_TICKERS = 8
HIGH_IV = 0.40  # what "high IV" means
NO_PRICE_CAP = 10_000.0  # "over $150" has no sensible upper bound

HELP_TEXT = (
    "🔭 *Mesa Prospector* — cash-secured put ideas for the wheel.\n\n"
    "*Try:*\n"
    "  `scan` — best puts from the latest shortlist\n"
    "  `scan all` — rebuild the shortlist from the S&P 500 + Nasdaq 100 (a few minutes)\n"
    "  `check DDOG NFLX` — specific tickers, with reasons if they don't qualify\n"
    "  `top 5 under $80`\n"
    "  `between $60 and $90`\n"
    "  `high IV` or `IV above 45%`\n\n"
    "Earnings inside the option's life are always excluded."
)

_TRIGGERS = (
    "find", "screen", "check", "show", "look", "search", "scan", "prospect",
    "pick", "idea", "what about", "how's", "hows", "how is", "wheel",
)
_UNIVERSE_WORDS = ("wide", "universe", "everything", "full scan", "s&p", "sp500", "scan all", "all stocks")

# All-caps words that are not tickers.
_STOP_UPPER = {
    "IV", "DTE", "OI", "ATM", "OTM", "ITM", "CSP", "CC", "EPS", "PE", "USD", "ETF", "OK", "US",
    "THE", "AND", "FOR", "WITH", "TOP", "ALL", "NO", "UP", "TO", "OF", "ON", "IN", "AT", "BY", "OR",
    "ME", "MY", "I", "A", "WIDE", "FIND", "SCAN", "SHOW", "CHECK", "SCREEN", "LOOK", "SEARCH",
    "UNDER", "OVER", "ABOVE", "BELOW", "BETWEEN", "HIGH", "LOW", "BEST", "PUTS", "PUT", "CALL",
    "CALLS", "WHAT", "ABOUT", "HOW", "IS", "ARE", "ANY", "SOME", "GOOD", "PLEASE", "QUALITY",
    "PICKS", "IDEAS", "MAX", "MIN", "UNIVERSE", "FULL", "EVERYTHING", "WHEEL", "SKIP", "EXCLUDE",
    "EARNINGS", "HELP", "START", "PCT", "THAN", "LESS", "MORE", "LEAST",
}
# Lowercase words that may follow "check"/"about" but are not tickers ("scan now" ≠ ServiceNow).
_STOP_LOWER = {
    "now", "please", "pls", "today", "tonight", "again", "stocks", "stock", "options", "anything",
    "something", "some", "the", "a", "an", "me", "my", "for", "on", "in", "at", "to", "of", "and",
    "with", "under", "over", "above", "below", "between", "top", "best", "no", "skip", "high",
    "low", "iv", "wide", "all", "puts", "put", "calls", "call", "ok", "than", "less", "more",
}

_NUM = r"(\d+(?:\.\d+)?)"
_IV_RE = re.compile(
    rf"\biv\b\s*(?:is\s*)?(?:above|over|>=?|at least|greater than|more than|min(?:imum)?)?\s*{_NUM}\s*%?"
)
_BETWEEN_RE = re.compile(rf"between\s*\$?\s*{_NUM}\s*(?:and|to|-|–)\s*\$?\s*{_NUM}")
_DOLLAR_RANGE_RE = re.compile(rf"\$\s*{_NUM}\s*(?:and|to|-|–)\s*\$\s*{_NUM}")
_UNDER_RE = re.compile(rf"(?:under|below|less than|up to|max(?:imum)?|<=?)\s*\$?\s*{_NUM}(?!\s*%)")
_OVER_RE = re.compile(rf"(?:over|above|more than|at least|min(?:imum)?|>=?)\s*\$?\s*{_NUM}(?!\s*%)")
_LIMIT_RES = (
    re.compile(rf"(?:top|best|first)\s*{_NUM}"),
    re.compile(rf"{_NUM}\s*(?:picks?|ideas?|results?|candidates?|names?|puts?)\b"),
)
_UPPER_TICKER_RE = re.compile(r"\b[A-Z]{1,5}(?:[.-][A-Z])?\b")
_LOWER_AFTER_TRIGGER_RE = re.compile(
    r"\b(?:check|about|how'?s|how is|look at|looking at)\s+((?:[a-z]{1,5}(?:[.-][a-z])?[\s,]*)+)"
)


def _extract_tickers(text: str, low: str) -> list[str]:
    found: list[str] = []

    def add(sym: str) -> None:
        sym = sym.upper()
        if sym not in found:
            found.append(sym)

    for m in _UPPER_TICKER_RE.finditer(text):
        if m.group(0) not in _STOP_UPPER:
            add(m.group(0))

    m = _LOWER_AFTER_TRIGGER_RE.search(low)
    if m:
        for word in re.split(r"[\s,]+", m.group(1).strip()):
            if not word or word in _STOP_LOWER:
                break
            add(word)

    return found[:MAX_TICKERS]


def parse_criteria(msg: str) -> dict | None:
    """Parse a screening request. Returns None if the message isn't one (caller shows help)."""
    text = msg.strip()
    low = text.lower()

    # --- IV first, and cut it out so "IV above 40%" is never read as a price ---------------
    iv_threshold = 0.0
    iv_match = _IV_RE.search(low)
    if iv_match:
        value = float(iv_match.group(1))
        iv_threshold = value / 100 if value > 1 else value
        low_wo_iv = low[: iv_match.start()] + " " + low[iv_match.end():]
    else:
        low_wo_iv = low
        if "high iv" in low:
            iv_threshold = HIGH_IV
    price_text = re.sub(rf"{_NUM}\s*%", " ", low_wo_iv)  # no percentage is ever a price

    # --- price band ------------------------------------------------------------------------
    price_min, price_max = SCAN_MIN_PRICE, SCAN_MAX_PRICE
    price_given = False
    rng = _BETWEEN_RE.search(price_text) or _DOLLAR_RANGE_RE.search(price_text)
    if rng:
        a, b = float(rng.group(1)), float(rng.group(2))
        price_min, price_max = min(a, b), max(a, b)
        price_given = True
    else:
        under = _UNDER_RE.search(price_text)
        over = _OVER_RE.search(price_text)
        if under:
            price_max = float(under.group(1))
            if price_max <= price_min:  # an explicit "under $30" beats the $50 default floor
                price_min = 0.0
            price_given = True
        if over:
            price_min = float(over.group(1))
            if price_min >= price_max:
                price_max = NO_PRICE_CAP
            price_given = True

    # --- limit -----------------------------------------------------------------------------
    limit = DEFAULT_LIMIT
    for rx in _LIMIT_RES:
        m = rx.search(price_text)
        if m:
            limit = max(1, min(MAX_LIMIT, int(float(m.group(1)))))
            break

    tickers = _extract_tickers(text, low)
    universe = any(w in low for w in _UNIVERSE_WORDS)

    bare_tickers = bool(tickers) and not re.search(r"[a-z]", text.replace("'", ""))
    recognised = (
        any(t in low for t in _TRIGGERS) or price_given or iv_match or "high iv" in low
        or universe or bare_tickers or re.search(r"\b(?:top|best)\s*\d", low)
    )
    if not recognised:
        return None

    return {
        "watchlist": tickers,
        "price_min": price_min,
        "price_max": price_max,
        "iv_threshold": iv_threshold,
        "exclude_earnings": True,  # always enforced by the engine (fail-closed)
        "limit": limit,
        "universe": universe and not tickers,
    }


def run_screen(criteria: dict, today: date | None = None) -> str:
    today = today or date.today()
    params = ScanParams(
        price_min=criteria["price_min"],
        price_max=criteria["price_max"],
        min_iv=criteria["iv_threshold"],
    )
    tickers: list[str] = criteria["watchlist"]

    if tickers:
        targets = [(t, t in WATCHLIST) for t in tickers]
        notes: list[str] = []
    else:
        targets, notes = default_targets(criteria["universe"])

    result = scan(targets, params, today, explain=bool(tickers))
    result.notes.extend(notes)

    # A full-universe scan at the default band doubles as the bot's shortlist refresh.
    if criteria["universe"] and not tickers and result.prefiltered:
        try:
            save_shortlist(result.prefiltered)
        except OSError as e:
            log.warning("Could not save shortlist: %s", e)

    return format_report(
        result,
        today,
        limit=criteria["limit"],
        max_misses=len(tickers) if tickers else 3,
        explain=bool(tickers),
    )


def respond(message: str, today: date | None = None) -> str:
    """Full pipeline: message → reply text (Markdown)."""
    criteria = parse_criteria(message)
    if criteria is None:
        return HELP_TEXT
    log.info("Screening: %s", criteria)
    return run_screen(criteria, today)
