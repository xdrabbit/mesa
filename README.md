# Mesa

Finds stocks worth researching for the **wheel strategy** (sell cash-secured puts → get assigned →
sell covered calls) and reports them over Telegram.

- **Prospector** — scans the S&P 500 + Nasdaq 100 for cash-secured-put candidates and sends the best
  one per ticker, ranked by a risk-adjusted score (not raw yield).
- **Telegram bot** — ask for ideas in plain English: `top 5 under $80`, `check DDOG NFLX`, `high IV`, `scan all`.
- **Watchdog** — monitors open positions: strike/breakeven proximity, DTE warnings, close-for-profit alerts.

Every put must pass the same gates whether it comes from the cron job or the bot: price band
($50–$110), market cap ≥ $10B, no biotech/crypto/non-REIT financials, solvency check, **no earnings
before expiry (unknown earnings date = excluded)**, 30–60 DTE, 0.20–0.35 delta (estimated from IV —
Yahoo supplies no greeks), cushion ≥ 5%, open interest ≥ 100, bid-ask spread ≤ 20%.
See `SCREENING_STRATEGY.md`.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env     # then fill in TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
```

`.env` is git-ignored. **Never commit a bot token** — this repository is public, and bots scrape GitHub
for them. If one leaks, revoke it in @BotFather (`/mybots` → API Token → Revoke).

## Usage

```bash
mesa prospector          # full scan → Telegram (what cron runs)
mesa screen under 80     # same engine, printed to the terminal
mesa screen check KO NOW
mesa bot                 # start the Telegram bot (answers TELEGRAM_CHAT_ID only)
mesa watchdog            # check open positions (add --summary for the full report)
mesa status              # print positions
```

## ⚠️ Run it during market hours

Outside 9:30–16:00 ET (7:30–14:00 MT) Yahoo returns **empty option quotes** (bid = ask = 0, junk IV), so no
put can qualify. Mesa detects this and says "market closed" instead of pretending nothing is
attractive. Schedule scans inside the window — see `crontab.example` and `setup-prospector-cron.sh`.

## The bot

```
scan                     best puts from the latest shortlist (fast)
scan all                 rebuild the shortlist from the full index list (a few minutes)
check DDOG NFLX          specific tickers — says *why* if one doesn't qualify
top 5 under $80          · between $60 and $90 · IV above 45% · high IV
```

- The shortlist is the set of tickers that passed the cheap prefilter on the last full scan (cron or
  `scan all`). Most mega-caps trade far above $110, so scanning only a hand-picked watchlist would
  almost always come back empty.
- Only the chat in `TELEGRAM_CHAT_ID` is answered; everyone else is ignored.
- One scan runs at a time. See `CONVERSATIONAL.md` for deployment (systemd, tmux).

## Positions

Open positions live in `positions.json` (edit directly). Prospector skips tickers you already hold a
position in, and Watchdog alerts on them. Mark a position `"closed": true` when it expires, is closed,
or is assigned.

## Tests

```bash
pytest                   # offline — no network, no Telegram
python scripts/verify_risk_filters.py
```
