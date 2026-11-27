"""Stock universe loader — S&P 500 (Wikipedia) + Nasdaq 100 (Nasdaq API, Wikipedia fallback).

Cache lives at mesa/data/universe.json with a 7-day TTL. Loading is fail-soft: either source
may be down (Wikipedia dropped its Nasdaq-100 constituents table in 2026), and a stale cache is
preferred over crashing the scan.

Also keeps a small "shortlist" cache (mesa/data/shortlist.json): the tickers that passed the
cheap prefilter on the last full scan, so the Telegram bot can answer quickly without
re-scanning ~500 names.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

CACHE_PATH = Path(__file__).resolve().parent / "data" / "universe.json"
CACHE_TTL = timedelta(days=7)
SHORTLIST_PATH = Path(__file__).resolve().parent / "data" / "shortlist.json"
SHORTLIST_MAX_AGE = timedelta(days=3)

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NDX_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
NDX_API_URL = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"
USER_AGENT = "Mozilla/5.0 (compatible; mesa-prospector/1.0)"

_FOOTNOTE_RE = re.compile(r"\s*\[[^\]]*\]\s*$")


def _normalize(ticker: str) -> str:
    # Wikipedia uses BRK.B / BF.B; yfinance uses BRK-B / BF-B.
    sym = _FOOTNOTE_RE.sub("", ticker.strip().upper())
    return sym.replace(".", "-")


def _get(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp.text


def _fetch_sp500() -> list[str]:
    soup = BeautifulSoup(_get(SP500_URL), "html.parser")
    table = soup.find("table", id="constituents") or soup.find("table", class_="wikitable")
    if table is None:
        raise RuntimeError("S&P 500 constituents table not found on Wikipedia")
    tickers: list[str] = []
    for row in table.find_all("tr")[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        sym = _normalize(cells[0].get_text())
        if sym and sym.replace("-", "").isalnum():
            tickers.append(sym)
    return tickers


def _fetch_ndx_api() -> list[str]:
    resp = requests.get(
        NDX_API_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json()["data"]["data"]["rows"]
    tickers = [_normalize(r["symbol"]) for r in rows if r.get("symbol")]
    tickers = [t for t in tickers if t and t.replace("-", "").isalnum() and len(t) <= 6]
    if len(tickers) < 50:
        raise RuntimeError(f"Nasdaq API returned only {len(tickers)} symbols")
    return tickers


def _fetch_ndx_wikipedia() -> list[str]:
    soup = BeautifulSoup(_get(NDX_URL), "html.parser")
    for table in soup.find_all("table", class_="wikitable"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        idx = next(
            (i for i, h in enumerate(headers) if h in ("ticker", "symbol")),
            None,
        )
        if idx is None:
            continue
        tickers: list[str] = []
        for row in table.find_all("tr")[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) <= idx:
                continue
            sym = _normalize(cells[idx].get_text())
            if sym and sym.replace("-", "").isalnum() and len(sym) <= 6:
                tickers.append(sym)
        if len(tickers) >= 50:
            return tickers
    raise RuntimeError("Nasdaq-100 constituents table not found on Wikipedia")


def _fetch_ndx() -> list[str]:
    try:
        return _fetch_ndx_api()
    except Exception as e:
        log.warning("Nasdaq API unavailable (%s); falling back to Wikipedia", e)
        return _fetch_ndx_wikipedia()


def fetch_universe() -> list[str]:
    """Union of whichever sources respond. Raises only if *both* fail."""
    tickers: set[str] = set()
    for name, fetch in (("S&P 500", _fetch_sp500), ("Nasdaq-100", _fetch_ndx)):
        try:
            got = fetch()
        except Exception as e:
            log.warning("%s source failed: %s", name, e)
            continue
        log.info("Fetched %d %s tickers", len(got), name)
        tickers.update(got)
    if not tickers:
        raise RuntimeError("Could not fetch any index constituents (S&P 500 and Nasdaq-100 both failed)")
    return sorted(tickers)


def load_universe(force_refresh: bool = False) -> list[str]:
    """Return cached universe, refreshing if missing or older than CACHE_TTL."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    stale: list[str] | None = None
    if CACHE_PATH.exists():
        try:
            data = json.loads(CACHE_PATH.read_text())
            fetched = datetime.fromisoformat(data["fetched_at"])
            cached = list(data["tickers"])
            if not force_refresh and datetime.now(timezone.utc) - fetched < CACHE_TTL:
                return cached
            stale = cached or None
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            log.warning("Universe cache unreadable, refreshing: %s", e)

    try:
        tickers = fetch_universe()
    except Exception as e:
        if stale:
            log.warning("Universe refresh failed (%s); using stale cache of %d tickers", e, len(stale))
            return stale
        raise
    CACHE_PATH.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "count": len(tickers),
                "tickers": tickers,
            },
            indent=2,
        )
    )
    log.info("Saved universe cache: %d tickers → %s", len(tickers), CACHE_PATH)
    return tickers


def save_shortlist(tickers: list[str]) -> None:
    """Remember which tickers passed the prefilter on the latest full scan."""
    SHORTLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    SHORTLIST_PATH.write_text(
        json.dumps(
            {
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "count": len(tickers),
                "tickers": sorted(set(tickers)),
            },
            indent=2,
        )
    )
    log.info("Saved shortlist: %d tickers → %s", len(tickers), SHORTLIST_PATH)


def load_shortlist() -> list[str]:
    """Tickers from the last full scan, or [] if missing/unreadable/older than SHORTLIST_MAX_AGE."""
    if not SHORTLIST_PATH.exists():
        return []
    try:
        data = json.loads(SHORTLIST_PATH.read_text())
        saved = datetime.fromisoformat(data["saved_at"])
        if datetime.now(timezone.utc) - saved > SHORTLIST_MAX_AGE:
            return []
        return list(data["tickers"])
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        log.warning("Shortlist cache unreadable, ignoring: %s", e)
        return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tix = load_universe(force_refresh=True)
    print(f"Universe: {len(tix)} unique tickers")
    print("First 30:", ", ".join(tix[:30]))
