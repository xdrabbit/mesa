# SPLIT_NOTES — separating the CSP/band baseline off the risk-hardening branch

## Goal

`feat/prospector-risk-hardening` had **two unrelated change-sets** bundled into its single
commit `efb919f`:

1. **Risk-hardening** (the intended work): full-life earnings exclusion, solvency gate,
   risk-adjusted ranking, plus tests/scripts/recon/build-notes. → **KEEP on the hardening branch.**
2. **A pre-existing baseline** (separately authored, not part of the hardening task): a
   covered-call → cash-secured-put conversion with a `$50–$110` price band, plus the
   conversational natural-language price-parsing edits. It was only bundled in because it was the
   working-tree state the recon line numbers were written against. → **MOVE to its own branch.**

This split makes the hardening branch contain **only** the hardening work, and preserves the
baseline losslessly on a new branch. **`main` was not touched, nothing was merged or deployed,
and no live scan/trade was run.**

---

## Commit shas — before / after

| Ref | Before | After |
|---|---|---|
| `main` | `c052b3e` | `c052b3e` (untouched) |
| `feat/prospector-risk-hardening` | `efb919f` (hardening **+** baseline, bundled) | `1c1e960` (hardening only, amended) |
| `feat/csp-conversion-band` | — (did not exist) | `498d0ad` (baseline only, off `main` `c052b3e`) |

- Pre-split hardening tip: `efb919f44cdb6c05dd259ac64a53426ce0dfec13`
- Post-split hardening tip: `1c1e9604375a1f84fbe29a1b5e52fecbc47d9bc0`
- CSP/band branch tip: `498d0adf605573d806d523b1604002686a548775` (base `c052b3e`)

The cleaned hardening branch was **force-pushed** (history rewrite: the single commit `efb919f`
was amended into `1c1e960`). `SPLIT_NOTES.md` itself is added as a separate follow-up commit so
the hardening fix commit stays purely hardening.

---

## What moved where

### Stayed on `feat/prospector-risk-hardening` (hardening only, layered on `main`)

- `mesa/prospector.py` — Fix 1 (per-expiration earnings gate, fail-closed: `_next_earnings_date`,
  `_earnings_admits`), Fix 2 (`_solvency_admits` + `SOLVENCY_DISTRESS`/`MIN_PROFIT_MARGIN`),
  Fix 3 (`Candidate` risk-score inputs + `score` via `scoring.risk_score`, `-c.annualized`
  tiebreaker). **Reverted to `main`'s covered-call scan and `main`'s price bands.**
- `mesa/scoring.py` — Fix 3: extracted `risk_score()` and made `_calculate_score` delegate to it.
  **Band/threshold constants reverted to `main`.**
- `tests/test_screening_logic.py` — trimmed to the 6 genuinely-hardening tests
  (2 earnings-gate + 4 solvency-gate).
- `scripts/verify_risk_filters.py`, `RECON_PROSPECTOR.md`, `BUILD_NOTES_PROSPECTOR.md`,
  `setup-prospector-cron.sh`, `.gitignore` — unchanged hardening deliverables (BUILD_NOTES got a
  small post-split accuracy update; see "Judgment calls").
- `mesa/conversational.py` — **reverted to `main`** (it was 100% baseline).

### Moved to `feat/csp-conversion-band` (baseline only, off `main`)

- `mesa/prospector.py` — scan **cash-secured puts** instead of covered calls (`chain.puts`,
  `strike < price`, `cushion = (price - strike)/price`, `abs(delta)`, annualized on strike);
  `$50–$110` price band; relaxed IV/DTE/delta/OI/spread/credit/cushion thresholds.
- `mesa/scoring.py` — `$50–$110` band + matching IV/DTE/delta/OI/spread thresholds (no
  `risk_score` extraction — that is hardening).
- `mesa/conversational.py` — `$50–$110` sweet-spot, natural-language price parsing
  (`between`/`under`/`over`), `price_min` plumbing, delta/premium passthrough.
- `tests/test_screening_logic.py` — the 3 baseline-exercising tests (1 put-selection +
  2 conversational-parsing) that were authored in `efb919f` but test baseline behavior; moved
  here so they are not lost (see "Judgment calls").

---

## How the hardening branch was verified pure and green

**Pure** — `git diff main` on the hardening branch shows only hardening:
- `mesa/prospector.py` vs `main`: only Fix 1/2/3 hunks (earnings gate, solvency gate, risk-score
  wiring). No `chain.puts`, no `price - strike`, no `abs(delta)`, no `$50/$110` band.
- `mesa/scoring.py` vs `main`: only the `risk_score()` extraction + `_calculate_score` delegation.
  Band/threshold constants identical to `main`.
- `mesa/conversational.py` vs `main`: **0 lines different** (identical).
- Grep for baseline markers (`chain.puts`, `price - strike`, `abs(float(delta`, `= 50.0`,
  `= 110.0`, `1.50`) across `mesa/*.py`: **no matches**.

**Green** — run offline (yfinance/telegram stubbed; mocked inputs; no network, no trades):

```
python3 -m unittest tests.test_screening_logic   # Ran 6 tests — OK
python3 scripts/verify_risk_filters.py           # REQUIRED: INTC excluded=True  SATS excluded=True  -> PASS
```

- `tests/test_screening_logic.py`: **6 tests pass** (was 9 pre-split; the 3 baseline tests moved
  to the CSP branch — see delta below).
- `verify_risk_filters.py`: **INTC → EXCLUDED** (earnings 2026-07-12 inside the ~45-DTE option
  life), **SATS → EXCLUDED** (distressed-credit solvency list). Also NODATA EXCLUDED
  (earnings-unknown fail-closed), REDINK EXCLUDED (margin -30% < -20%), MSFT & THINPM ADMITTED.
- The CSP branch was independently verified: `python3 -m unittest tests.test_screening_logic`
  on `feat/csp-conversion-band` → **3 tests pass**, and `mesa/{prospector,scoring,conversational}.py`
  byte-compile.

**Test-count delta (documented, as the task allows):** 9 tests pre-split → **6 on the hardening
branch + 3 on the CSP branch**. No test was deleted; each test now lives with the code it
exercises. The hardening branch lost the put-selection test (`test_scores_cash_secured_puts...`,
which asserts a put at strike 90 — baseline behavior) and the two `ConversationalCriteriaTest`
tests (which exercise the baseline NL price-parsing). Those three are green on the CSP branch.

---

## Overlap / judgment calls (the change-sets were intertwined, not cleanly hunk-separable)

The two change-sets were **interleaved within the same files and sometimes the same hunks**, so a
pure `git revert`/cherry-pick of hunks was not possible. Separation was done by reconstructing
each file's target state. Clean separation was achievable, but required these judgment calls:

1. **`mesa/prospector.py` and `mesa/scoring.py` mixed both sets in adjacent lines.** Example: the
   constants hunk changed the price band (baseline) *and* removed `PREFILTER_MIN_DAYS_TO_EARNINGS`
   (Fix 1) together; the `_score_options` body flipped calls→puts (baseline) *and* added the
   earnings gate + risk-score field population (hardening) in the same function. I split by
   category: hardening logic kept verbatim, baseline (calls/puts, bands, filter thresholds)
   reverted to `main`.

2. **`risk_score()` kept exactly as authored in `efb919f`.** Its internal liquidity normalizers
   (`oi / 500.0`, `spread_pct / 0.20`) and hardcoded `0.50` IV center differ from `main`'s
   pre-extraction inline `_calculate_score` (`oi / 1000.0`, `spread_pct / 0.05`, IV center derived
   from `MIN_IV`/`MAX_IV`). I treated `risk_score` as the Fix 3 deliverable and did **not** re-tune
   it, so this is a deliberate, behavior-affecting choice. Impact is bounded: it changes only score
   **magnitudes/scaling**, not which candidates pass the filters, and **no test asserts an exact
   score**. (BUILD_NOTES' "byte-identical (58.75 == 58.75)" line is the original author's claim
   about the extraction in the `efb919f` working tree; left as-authored.)

3. **The test file is a hardening deliverable but contained baseline tests.**
   `test_scores_cash_secured_puts_below_current_price` and the two `ConversationalCriteriaTest`
   tests assert baseline (puts / NL-parsing) behavior and would fail on a pure-hardening branch.
   Rather than delete them (lossy) or leave the branch red, I **moved** them to the CSP branch
   alongside the code they test. The two earnings-gate tests were kept on the hardening branch;
   they exclude an expiration **before** the option chain is read, so they pass regardless of
   calls-vs-puts (their fixtures were simplified to empty chains to avoid implying puts).

4. **Docstrings/comments were reconstructed, not independently recovered.** The original baseline
   was an *uncommitted* working tree; only its `efb919f`-merged form exists. Docstring wording on
   each branch was set to match that branch's behavior (covered-call wording on hardening, CSP
   wording on the baseline branch).

5. **Minor, non-functional residue left as-authored on the hardening branch:**
   - `mesa/scoring.py` has 3 blank-line whitespace-only diffs vs `main` (trailing spaces dropped
     on blank lines I edited) — cosmetic, no behavior impact.
   - `BUILD_NOTES_PROSPECTOR.md` / `RECON_PROSPECTOR.md` retain some baseline-era narrative
     (e.g. a "30–60 DTE" strategy framing; recon line-number references against the baseline
     working tree). These are descriptive context for the hardening rationale; I updated the
     concrete, now-false claims (the bundled-baseline note and the "9 tests" count → 6) and left
     the strategy narrative intact per the task's "leave BUILD_NOTES intact" instruction.

None of these rose to "genuinely impossible to separate," so per the task I proceeded and
documented the calls here rather than stopping for human review.

---

2026-06-22 07:12:56 MDT — Branch split complete — hardening branch is now pure, CSP/band edits preserved on feat/csp-conversion-band, main untouched.
