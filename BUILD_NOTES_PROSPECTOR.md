# Build Notes — Prospector Risk-Hardening

Branch: `feat/prospector-risk-hardening` (off `main`, **not merged**).
Scope: the three fixes from the task brief, plus a verification script and regression tests.
No live scans were run; no trades placed or logged. yfinance is **not installed** in the build
environment, so all verification used **mocked** inputs (see "Data-field verification" below).

---

## Files changed

| File | Change |
|---|---|
| `mesa/scoring.py` | Extracted the weighted-blend math into a reusable module-level `risk_score(...)`; `Candidate._calculate_score` now delegates to it (behavior unchanged — verified identical). |
| `mesa/prospector.py` | Fix 1 (earnings: full option life + fail-closed), Fix 2 (solvency gate), Fix 3 (route ranking through `scoring.risk_score`). |
| `tests/test_screening_logic.py` | Earnings-gate + solvency-gate regression tests (6). After the split, the baseline put-selection and conversational-parsing tests moved to `feat/csp-conversion-band`. |
| `scripts/verify_risk_filters.py` | **New.** Standalone, mock-based ADMITTED/EXCLUDED reporter incl. INTC + SATS. |
| `BUILD_NOTES_PROSPECTOR.md` | **New.** This file. |

> Note (updated post-split): this branch **previously** also carried a pre-existing baseline
> (a covered-call → cash-secured-put conversion and `$50–$110` price-band retune across
> `prospector.py`, `scoring.py`, `conversational.py`) that the recon was written against. That
> baseline has since been **split off** onto its own branch, `feat/csp-conversion-band` (off
> `main`), so it is no longer here — this branch now carries **only** the risk-hardening work
> (the three fixes below, layered directly on `main`). See `SPLIT_NOTES.md` at the repo root for
> the exact before/after commit shas and verification. `prospector.py` therefore scans covered
> calls on `main`'s `$70–$120` scan band again; the hardening logic is independent of calls vs.
> puts and of the band values.

---

## Fix 1 — Earnings exclusion: full option life + fail CLOSED

**Before:** a single prefilter check, `_next_earnings_within(t, 14)`, excluded only when earnings
fell within a fixed **14-day** window AND the helper returned exactly `True`. Missing/None data
→ admitted (**fail open**). Options are sold at 30–60 DTE, so earnings 15–60 days out slipped
through inside the option's life (the INTC failure mode).

**After:**
- New `_next_earnings_date(t) -> date | None` returns the soonest **upcoming** earnings date, or
  `None` when the source is blind (no calendar, exception, only past dates).
- New pure gate `_earnings_admits(earnings_date, exp_date) -> (admit, reason)`:
  - `earnings_date is None` → `(False, "earnings-unknown-excluded")` — **fail closed**.
  - `earnings_date <= exp_date` → excluded, reason `"earnings <date> on/before exp <date>"`.
  - else admitted.
- Wired **per-expiration** inside `_score_options`: earnings is resolved once, then each
  expiration in the 30–60 DTE band is gated against its **actual** `exp_date` (not a fixed
  window). The old 14-day prefilter check and `PREFILTER_MIN_DAYS_TO_EARNINGS` constant were
  removed; `_next_earnings_within` was replaced.

Sort/scan-logic before/after lives under Fix 3.

## Fix 2 — Solvency / quality gate (new)

**Before:** no fundamentals/solvency/credit filter of any kind. A name missing debt payments
(SATS) cleared every gate.

**After:** new pure gate `_solvency_admits(info, ticker) -> (admit, reason)`, called in
`_prefilter` (cheap `.info` stage). Exclusions, in order:
1. `ticker in scoring.ACCOUNTING_ISSUES` (imported from `mesa/scoring.py`; currently an empty
   set, honored for forward-compat) → `"solvency-excluded: accounting/SEC watchlist"`.
2. `ticker in SOLVENCY_DISTRESS` — a new manual distressed-credit list seeded with `SATS`
   (EchoStar, per `RECON_PROSPECTOR.md §6`) → `"solvency-excluded: distressed-credit watchlist"`.
3. `profitMargins < MIN_PROFIT_MARGIN` (`-0.20`) → `"solvency-excluded: profit margin … < -20%"`.

**Deliberate fail-OPEN on `profitMargins` missing:** a >$10B name without the field is *not*
auto-excluded, because the field is occasionally absent on healthy names and over-exclusion is
costly. The **hard** solvency gate is the two manual sets; the margin is a best-effort proxy.
(Contrast Fix 1, which fails *closed* — earnings risk is binary and time-bounded, so unknown
is treated as unsafe.)

## Fix 3 — Risk-adjusted ranking: route the cron through `scoring.py`

**Before** (`prospector.py`):
```python
@property
def score(self) -> float:
    return self.annualized                       # raw annualized yield — the only metric
...
# Priority hits float to top, then sort by annualized return.
all_candidates.sort(key=lambda c: (not c.is_priority, -c.score))
```

**After:**
```python
@property
def score(self) -> float:                        # risk-adjusted 0-100 blend
    return risk_score(
        premium_pct_of_strike=self.premium_pct_of_strike,
        cushion_pct=self.cushion_pct, iv=self.iv, oi=self.oi, spread_pct=self.spread_pct,
    )
...
# Priority hits float to top (is_priority ordering preserved), then risk score, then annualized.
all_candidates.sort(key=lambda c: (not c.is_priority, -c.score, -c.annualized))
```

- The blend is `mesa.scoring.risk_score` (premium 40% / cushion 30% / IV-centeredness 20% /
  liquidity 10%) — the same function the conversational screener now uses, so the automated scan
  and `mesa screen` share one ranking. `scoring.Candidate._calculate_score` delegates to it;
  verified the refactor leaves its output byte-identical (`58.75 == 58.75`).
- **Annualized yield is preserved** — still computed, still shown in `format_line`, and kept as
  the **sort tiebreaker** (`-c.annualized`). It is now one input among several, not the sole key.
- **`is_priority` ordering preserved**: the leading `not c.is_priority` sort term is unchanged,
  so the 20 watchlist names still float to the top.
- `Candidate` gained `cushion_pct`, `oi`, `spread_pct`, `premium_pct_of_strike` (PERCENT scale)
  so it can feed the blend; `_score_options` now populates them.

---

## Data-field verification

| Field | Used by | Available? |
|---|---|---|
| `Ticker.calendar` → `"Earnings Date"` | Fix 1 earnings | Same source the prior code already read; **not live-verified** here (yfinance not installed). Fix 1 **fails closed** precisely because this field is frequently empty/None. |
| `info["profitMargins"]` | Fix 2 margin proxy | Documented yfinance `.info` field (per recon). **Not live-verified** in this env; gate **fails open** when absent, so a missing field cannot cause a bad exclusion. |
| `info["sector"]`, `info["industry"]`, `info["country"]`, `info["marketCap"]`, `info["currentPrice"]` | existing prefilter | Pre-existing usage, unchanged. |
| option-row `strike/bid/ask/openInterest/impliedVolatility/delta` | existing scoring | Pre-existing usage, unchanged. |

**Because yfinance is not installed in the build environment, no live market-data call was made.**
All verification is mock-based (`scripts/verify_risk_filters.py` + unit tests). Field availability
is asserted from `RECON_PROSPECTOR.md` and yfinance's documented `.info` schema, **not** confirmed
against a live response here.

## TODOs / limitations / blocked items

- **No fix was blocked.** All three were implemented.
- **`debtToEquity` / `freeCashflow` / interest-coverage NOT gated** (intentionally). These were
  candidate solvency signals in the recon, but: `freeCashflow` is routinely negative for healthy
  growth names (would over-exclude), and `debtToEquity` is sparse, scale-dependent, and
  sector-relative. Gating on them reliably needs data the free yfinance feed doesn't cleanly
  provide. We gate on the cleaner `profitMargins` proxy + manual lists instead, rather than
  fabricate thresholds. **TODO:** if a fundamentals provider with normalized credit metrics
  (interest coverage, rating) is added, extend `_solvency_admits`.
- **"Missed/deferred debt-interest payment" has no clean yfinance field** (the literal SATS
  signal). Sourced out-of-band and hard-listed in `SOLVENCY_DISTRESS`. **TODO:** wire a credit/
  ratings feed so this list isn't hand-maintained.
- **Earnings data quality** is only as good as `Ticker.calendar`. Fail-closed means names with
  no calendar data are excluded — this is intentional and may reduce candidate count when
  yfinance is sparse. **TODO (optional):** add a second earnings source before relaxing.
- `MIN_PROFIT_MARGIN = -0.20` and the moderate-IV center (0.50) are heuristics inherited from
  `scoring.py`; tune with backtest data.

## How to verify (offline, no trades)

```
python3 scripts/verify_risk_filters.py          # prints ADMITTED/EXCLUDED + reason per ticker
python3 -m unittest tests.test_screening_logic   # 6 tests, all pass (post-split; was 9 pre-split)
```

Observed result: INTC EXCLUDED (earnings inside option life), SATS EXCLUDED (distressed-credit
list), NODATA EXCLUDED (earnings-unknown fail-closed), REDINK EXCLUDED (margin -30%), MSFT &
THINPM ADMITTED. Ranking demo: the high-IV/fat-premium row ranks **last**, not first.

---

Build timestamp: 2026-06-22 (America/Denver).

Prospector hardening build complete — branch feat/prospector-risk-hardening, not merged.
