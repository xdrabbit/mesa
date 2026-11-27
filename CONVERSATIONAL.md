# Mesa Telegram bot

Ask Mesa for wheel-strategy put ideas from Telegram, in plain English. The bot is a thin front end
over the same engine the cron job uses (`mesa/prospector.py`), so every gate in
`SCREENING_STRATEGY.md` — including the earnings and solvency checks — applies to chat answers too.

## What you can say

| You type | What happens |
|---|---|
| `scan` | Best puts from the latest **shortlist** (fast, ~seconds) |
| `scan all` | Rescans the full S&P 500 + Nasdaq 100 and **rebuilds the shortlist** (a few minutes) |
| `check DDOG NFLX` / `check ddog nflx` / `how's JPM` | Just those tickers; tickers that don't qualify get a reason |
| `top 5 under $80` | Up to 5 picks, price ceiling $80 |
| `between $60 and $90`, `$60 to $90` | Price band |
| `over $150`, `under 30` | An explicit bound replaces the matching default ($50–$110) |
| `IV above 45%`, `iv 0.45`, `high iv` | Minimum implied volatility (`high iv` = 40%) |
| anything else | Help text |

Defaults: price $50–$110, 3 picks (max 10). **Earnings inside the option's life are always excluded** —
there is no switch to turn that off.

Each pick shows what you need to place the trade (illustrative numbers):

```
1. KO $97.59 — sell 2027-01-15 $90 put (49d, Δ0.24)
   $1.45 credit · 1.6% of strike · 13% ann. · cushion 8% · IV 28% · OI 1,204 · score 61/100
```

## How it fits together

```
Telegram ──► mesa/webhook.py ──► mesa/conversational.py ──► mesa/prospector.py (shared engine)
 (polling)    auth + threading     parse text → ScanParams     prefilter → option chains → gates → rank
                                                                      │
cron ──► mesa prospector ─────────────────────────────────────────────┘ ──► mesa/data/shortlist.json
```

- **Shortlist** (`mesa/data/shortlist.json`, git-ignored): tickers that passed the cheap prefilter on
  the last full scan (cron or `scan all`), valid for 3 days. Most large caps trade well above $110, so
  scanning only a hand-picked list would nearly always return nothing.
- **Access control:** only the chat whose id equals `TELEGRAM_CHAT_ID` is answered; all others are
  ignored (and logged). The bot refuses to start without a chat id.
- **One scan at a time**; a second request gets "already running". Scans run in a worker thread so the
  bot stays responsive.
- **Market hours matter.** Yahoo returns empty option quotes outside 9:30–16:00 ET; Mesa then replies
  with a "market closed" note instead of a misleading "nothing qualifies".

## Setup

```bash
cp .env.example .env     # fill in TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
pip install -e ".[dev]"
mesa bot                 # or ./run-bot.sh
```

**Secrets belong in `.env` only** (git-ignored). This repository is public — never paste a token into
a doc, issue or commit. To rotate a token: @BotFather → `/mybots` → your bot → API Token → Revoke,
update `.env`, restart the bot.

Find your chat id: send your bot any message, then open
`https://api.telegram.org/bot<TOKEN>/getUpdates` and read `"chat":{"id": ...}`.

## Running it

```bash
./run-bot.sh &                        # background; logs to /tmp/mesa-bot.log
```

systemd (edit `User=` and the paths in `mesa-bot.service` to match your machine):

```bash
sudo cp mesa-bot.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now mesa-bot
journalctl -u mesa-bot -f
```

## Troubleshooting

- **No reply at all** — wrong `TELEGRAM_CHAT_ID` (the log says `Ignoring message from unauthorized chat <id>`),
  or the bot isn't running (`tail -f /tmp/mesa-bot.log`).
- **"market is probably closed"** — scan between 9:30 and 16:00 ET.
- **"No shortlist yet"** — send `scan all` once (or let the cron job run) to build it.
- **Empty results during market hours** — the gates are deliberately strict; the "Nearest misses" list
  names the dominant blocker per ticker. Try `check TICKER` to see exactly why one name is out.
- **Yahoo `429` / "Invalid Crumb" in the log** — transient rate limiting; Mesa retries `.info` lookups
  and warms up Yahoo's session before fanning out threads.

## Tuning

All thresholds are constants at the top of `mesa/prospector.py` (`SCAN_MIN_PRICE`, `TARGET_DTE_MIN`,
`TARGET_DELTA_MIN/MAX`, `MIN_OPEN_INTEREST`, `MAX_BID_ASK_SPREAD_PCT`, `CORE_MIN_CUSHION`, …). The
priority watchlist (`WATCHLIST`) only sets which tickers are marked ⭐ and ranked first.
