# Prospector Recon — Cash-Secured-Put Candidate Selection

**Scope:** Read-only investigation of how the MESA Prospector selects and ranks
cash-secured-put candidates. Triggered by tonight's top picks (INTC, SATS, NFLX)
looking risk-blind.

---

## 0. Critical finding up front

There are **two** scoring systems in the repo, and the dangerous one is the one that runs:

| Module | Used by | Ranking logic | Earnings? | Solvency? |
|---|---|---|---|---|
| `mesa/prospector.py` | **The cron scan** (`mesa.cli prospector`) → the "TOP PICKS" Telegram alert | **Pure annualized premium yield** | partial / fail-open | none |
| `mesa/scoring.py` | `conversational.py` (`mesa screen`) + `morning_brief.py` only | Traffic-light 🟢/🟡/🔴 with weighted 0–100 score | yellow-flag | empty stub |

The richer, safer traffic-light logic in `scoring.py` (which *does* yellow-flag earnings
proximity, has an `ACCOUNTING_ISSUES` set, and weights cushion/IV) **is never invoked by the
automated scan.** The scan that produced INTC/SATS/NFLX is `prospector.py`, which sorts purely
by annualized yield. This is the heart of the problem.

---

## 1. Prospector entry point

- **Active scanner:** `mesa/prospector.py` — function `run()` (lines 310–367).
- **Wiring:** `mesa/cli.py:48-50` maps the `prospector` subcommand to `mesa.prospector.run`.
- **Trigger:** `setup-prospector-cron.sh:41` runs `python3 -m mesa.cli prospector` three times
  every weekday (07:30, 12:00, 16:30 MT). Output is emitted via Telegram (`send(...)`).
- **Universe loader:** `mesa/universe.py` — `load_universe()`.
- **Unused alternate:** `mesa/scoring.py` (traffic-light) is imported only by
  `conversational.py:16` and `morning_brief.py:25`, *not* by `prospector.py`.

---

## 2. Universe

Defined in `mesa/universe.py`. Two layers:

1. **Priority watchlist** — 20 hardcoded names (`prospector.py:29-33`): AAPL, MSFT, NVDA, GOOGL,
   AMZN, META, TSLA, JPM, V, MA, UNH, JNJ, PG, KO, WMT, HD, ABBV, DDOG, CRM, NOW. These are
   scanned first and marked ⭐, and **float to the top of the ranking regardless of score**
   (`prospector.py:346`).
2. **Full universe** — S&P 500 ∪ Nasdaq-100, scraped from Wikipedia and cached at
   `mesa/data/universe.json` with a 7-day TTL (`universe.py:18-19, 86-110`).

Current cache: **516 unique tickers** (fetched 2026-06-15). INTC, SATS, and NFLX are all
present in the cached universe.

---

## 3. Pre-filters (applied BEFORE ranking)

Two filter stages: a cheap `.info`-only **prefilter** (`_prefilter`, lines 156–190) and an
in-loop **option-row filter** inside `_score_options` (lines 193–299).

### 3a. Earnings-date proximity — PRESENT but FAIL-OPEN ⚠️

```python
# prospector.py:133-153
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
    ...
```

```python
# prospector.py:187-188
if _next_earnings_within(t, PREFILTER_MIN_DAYS_TO_EARNINGS) is True:
    return None, None, f"earnings within {PREFILTER_MIN_DAYS_TO_EARNINGS}d"
```

Threshold: `PREFILTER_MIN_DAYS_TO_EARNINGS = 14` (line 39).

**Two serious holes:**
1. The check only excludes when the helper returns **exactly `True`**. If yfinance returns
   `None` (no calendar data, exception, empty) — common on many names — the ticker **passes
   through**. The filter is blind whenever the data source is.
2. The window is only **14 days**, but the option DTE target is **30–60 days**
   (line 55-56). So an earnings event 20 days out is *inside* the option's life but *outside*
   the 14-day exclusion → the candidate is sold an option that **spans its earnings report**.
   This is exactly the INTC failure mode.

### 3b. Implied-volatility ceiling — PRESENT but effectively wide open ⚠️

```python
# prospector.py:54-55, 244-250
MIN_IV = 0.0
MAX_IV = 1.50
...
if not (MIN_IV <= iv <= MAX_IV):
    if best_miss is None:
        best_miss = NearMiss(...)
    continue
```

Range is **0% to 150% IV**. This does not "cap" risk in any practical sense — it admits
almost everything. Worse, because ranking is annualized yield (§4), high IV is *rewarded*, not
penalized. There is **no preference for moderate IV** in the scan (the `scoring.py` "closer to
50% is better" logic is not used here).

### 3c. Liquidity / bid-ask spread — PRESENT ✅

```python
# prospector.py:262-274
bid = float(row.get("bid", 0) or 0)
ask = float(row.get("ask", 0) or 0)
oi  = int(row.get("openInterest", 0) or 0)
...
if bid <= 0 or ask <= 0:
    continue
if oi < MIN_OPEN_INTEREST:          # 100
    continue
spread_pct = (ask - bid) / bid
if spread_pct > MAX_BID_ASK_SPREAD_PCT:   # 0.20
    continue
```

Thresholds: `MIN_OPEN_INTEREST = 100`, `MAX_BID_ASK_SPREAD_PCT = 0.20` (lines 60-61). This is
a real and reasonable filter.

### 3d. Price band / market-cap floor — PRESENT ✅ (but note caveat)

```python
# prospector.py:171-181
market_cap = info.get("marketCap")
if not market_cap:
    return None, None, "no market cap data"
if market_cap < PREFILTER_MIN_MARKET_CAP:        # 1e10 = $10B
    return None, None, f"cap ${market_cap / 1e9:.1f}B < $10B"

price = info.get("currentPrice") or info.get("regularMarketPrice")
if not price:
    return None, None, "no price data"
if price < PREFILTER_MIN_PRICE or price > PREFILTER_MAX_PRICE:   # 50.0 .. 110.0
    return None, None, f"price ${price:.2f} outside ..."
```

Thresholds: price **$50–$110**, market cap **≥ $10B** (lines 36-38). This is a *capital-sizing*
constraint (keep one contract ≈ $5k–$11k of collateral), **not a quality filter**. A $10B+
company can still be in financial distress, and the band is re-checked in `_score_options`
(lines 197-201).

> Caveat worth flagging: the $50–$110 band is narrow. Whether INTC/SATS/NFLX truly sat inside
> it at scan time depends on their live prices; if any were outside, they'd have been rejected
> at prefilter. The criteria analysis below holds regardless — the point is *what the code
> screens for*, and quality/solvency is not among them.

### 3e. Sector / fundamentals / solvency — PARTIAL; **NO solvency filter** ❌

```python
# prospector.py:117-130
def _passes_sector_filter(info: dict) -> tuple[bool, str]:
    sector = (info.get("sector") or "").strip()
    industry = (info.get("industry") or "").strip().lower()
    for kw in EXCLUDED_INDUSTRY_KEYWORDS:        # biotech, pharma, drug mfrs, crypto
        if kw in industry:
            return False, f"industry: {info.get('industry')}"
    if sector == "Financial Services" and "reit" not in industry:
        return False, "Financial Services (non-REIT)"
    return True, ""
```

Plus a substring blocklist (`prospector.py:41`): `MARA, RIOT, MSTR, COIN, SQQQ, TQQQ, UVXY`,
and a country check (US-only, line 167-169).

What exists: keyword/sector **category** exclusions and a hand-maintained ticker blocklist.
What is **entirely absent**: any look at **debt, interest coverage, credit rating, leverage,
profitability, or solvency**. Nothing reads `info` fields like `debtToEquity`,
`totalDebt`, `freeCashflow`, `profitMargins`, etc. A company **skipping debt-interest
payments (SATS) passes every filter** as long as it's US, >$10B cap, in-band price, and not in
an excluded sector.

(Note: `scoring.py:20` has `ACCOUNTING_ISSUES = set()` — an empty stub for exactly this kind of
manual exclusion — but it's in the *unused* module and is empty anyway.)

### 3f. Delta / strike-distance (OTM cushion) — PARTIAL ⚠️

```python
# prospector.py:236-260
cushion = (price - strike) / price
if cushion < tier_cushion:        # 0.05 for both tiers
    continue
...
# Delta is optional — many yfinance chains don't include it.
delta_raw = row.get("delta")
if delta_raw is not None and delta_raw == delta_raw:  # NaN-safe
    delta = abs(float(delta_raw))
    if not (TARGET_DELTA_MIN <= delta <= TARGET_DELTA_MAX):  # 0.20..0.35
        continue
```

- **Cushion floor:** only **5%** OTM (`CORE_MIN_CUSHION = NON_CORE_MIN_CUSHION = 0.05`, lines
  64-67). Both tiers are identical despite the comment claiming differentiation.
- **Delta band 0.20–0.35 is best-effort only.** yfinance rarely populates `delta`, so in
  practice this filter is usually skipped and strike distance is governed solely by the thin 5%
  cushion. A high-IV name can therefore have a 5%-OTM strike that is still a high-probability
  assignment.

Also enforced in the row loop: DTE 30–60 (lines 219-220), strike < price (233-234), premium
≥ 1% of strike (`MIN_PREMIUM_PCT_OF_STRIKE = 0.01`, lines 59, 283), and a per-tier minimum
credit (both set to `0.0`, lines 64-67 — i.e. no real floor).

---

## 4. Ranking / scoring formula — **THE KEY ANSWER**

The "top picks" are produced by sorting on a single number: **annualized premium yield.**

```python
# prospector.py:286 — the only metric computed for ranking
annualized = (mid / strike) * (365 / dte) if dte > 0 else 0.0
```

```python
# prospector.py:85-87 — score is literally just the annualized figure
@property
def score(self) -> float:
    return self.annualized
```

```python
# prospector.py:346-347 — the sort that picks the winners
# Priority hits float to top, then sort by annualized return.
all_candidates.sort(key=lambda c: (not c.is_priority, -c.score))
top_picks = all_candidates[:TOP_N]   # TOP_N = 5
```

**What it optimizes for:** annualized return-on-collateral = `(mid_premium / strike) × (365 / dte)`.
That is **premium yield and nothing else.** There is:

- **no probability-of-profit / delta weighting** in the score,
- **no IV-quality term** (high IV is pure upside to the score),
- **no risk/quality penalty** of any kind,
- only a coarse tie-break that floats the 20 watchlist names above everything.

Because IV is the dominant driver of option premium, **ranking by annualized yield is
mathematically equivalent to ranking by "fattest premium / highest fear."** The names with the
biggest premiums are precisely the names the market is most afraid of — earnings straddlers and
distressed credits. The scanner is built to find them and call them "best."

> Contrast: the unused `scoring.py:151-186` `_calculate_score()` blends premium 40% / cushion
> 30% / IV-centeredness 20% / liquidity 10% and yellow-flags earnings — a far safer ranking
> that the automated scan does not use.

---

## 5. Earnings data source

Yes, but thin and fail-open. The only source is **`yfinance`'s `Ticker.calendar`** (`Earnings
Date` field), read in `_next_earnings_within` (`prospector.py:133-153`). There is no dedicated
earnings-calendar API, no caching, and no fallback when yfinance returns nothing. When the
calendar is missing or errors, the function returns `None` and the name is **treated as
earnings-safe** (see §3a). So the scanner is *partially* earnings-aware but effectively **blind
whenever yfinance lacks data, and structurally blind to earnings landing 15–60 days out** (the
14-day window is shorter than the 30–60-day option life it sells).

---

## 6. Verdict

### Why INTC / SATS / NFLX rank highly under the current criteria

All three are (or were at scan time) **high-IV names**, and high IV is exactly what the ranking
rewards. The single sort key is annualized premium yield (`(mid/strike)×(365/dte)`), which is
maximized by the fattest premiums, which are maximized by the highest implied volatility — i.e.
the market's fear. The scanner has no offsetting risk term, so the riskiest names rise to the
top *by design*:

- **INTC (earnings straddler):** Premium is inflated by pre-earnings IV. The earnings filter is
  only 14 days wide while options are sold at 30–60 DTE, so an earnings date inside the option's
  life but ≥14 days out **does not exclude it**. If yfinance's calendar is empty for INTC, even
  a near-term event slips through (fail-open). The fat pre-earnings premium then *wins* the
  ranking instead of being penalized.
- **SATS (solvency risk — skipping debt interest):** There is **no fundamentals/solvency/credit
  filter at all.** SATS clears every gate (US, >$10B cap, in-band price, non-excluded sector),
  and its distress-driven IV produces a huge premium that the yield ranking loves. The system
  literally cannot see that the company is missing debt payments.
- **NFLX (high-IV momentum name):** Same mechanism — large premiums from elevated IV, the
  150% IV ceiling admits it, and yield ranking promotes it. The only real brakes (liquidity,
  5% cushion) are easily cleared by a liquid mega-cap.

### Specific missing / mis-weighted filters

1. **Ranking is single-metric (annualized yield) with zero risk adjustment** — the root cause.
2. **Earnings window (14d) is shorter than the option life (30–60d)** — lets earnings land
   inside the contract.
3. **Earnings check is fail-open** — missing yfinance data ⇒ treated as safe.
4. **No solvency/credit/fundamentals filter whatsoever** — distressed names pass freely.
5. **IV ceiling of 150% is meaningless as a risk cap**, and IV quality is absent from the score
   (high IV is rewarded, not moderated).
6. **Delta filter is best-effort and usually skipped**; the only real strike-distance control is
   a thin 5% cushion identical across both tiers.
7. **The safer `scoring.py` traffic-light logic exists but is not wired into the scan.**

### Smallest set of changes to fix it (described only — NOT implemented)

1. **Extend the earnings exclusion to cover the option's life and fail *closed*.** Reject if any
   earnings date falls on/before the *expiration being scored* (compare to `exp_date`, not a
   fixed 14-day window), and exclude (or down-rank) names where earnings data is unknown rather
   than admitting them. This single change screens INTC-type straddlers.
2. **Add a fundamentals/solvency gate in `_prefilter`** using fields already on `info` (e.g.
   reject on very high `debtToEquity`, negative `freeCashflow`/`profitMargins`, or low interest
   coverage), plus repopulate a manual distressed-name blocklist (the `ACCOUNTING_ISSUES`-style
   set). This screens SATS-type credit risk.
3. **Replace the single-metric sort with a risk-adjusted score.** Smallest viable version: reuse
   the existing `scoring.py` weighted blend (premium / cushion / IV-centeredness / liquidity) —
   or at minimum multiply annualized yield by a probability-of-keeping-premium proxy (cushion or
   `1 − delta`) and add an IV-moderation penalty so that fear-driven premium stops being pure
   upside. This stops the ranking from preferring the scariest names.

Optional/cheap hardening: tighten `MAX_IV` to a realistic ceiling (e.g. ~0.80) and raise the
cushion floor above 5% so a thin OTM distance can't qualify a high-IV name.

These three changes are additive to the existing prefilter and sort; they do not require
re-architecting the pipeline.

---

*Recon generated 2026-06-22 06:30 MDT.*

**Prospector recon complete — no code modified.**
