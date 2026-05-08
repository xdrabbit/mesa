"""Stock universe loader — S&P 500 + Nasdaq 100 from Wikipedia, cached locally.

Cache lives at mesa/data/universe.json with a 7-day TTL.
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

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NDX_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
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


def _fetch_ndx() -> list[str]:
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


def fetch_universe() -> list[str]:
    sp500 = _fetch_sp500()
    ndx = _fetch_ndx()
    log.info("Fetched %d S&P 500 + %d Nasdaq-100 tickers", len(sp500), len(ndx))
    return sorted(set(sp500) | set(ndx))


def load_universe(force_refresh: bool = False) -> list[str]:
    """Return cached universe, refreshing if missing or older than CACHE_TTL."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not force_refresh and CACHE_PATH.exists():
        try:
            data = json.loads(CACHE_PATH.read_text())
            fetched = datetime.fromisoformat(data["fetched_at"])
            if datetime.now(timezone.utc) - fetched < CACHE_TTL:
                return list(data["tickers"])
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            log.warning("Universe cache unreadable, refreshing: %s", e)

    tickers = fetch_universe()
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tix = load_universe(force_refresh=True)
    print(f"Universe: {len(tix)} unique tickers")
    print("First 30:", ", ".join(tix[:30]))
