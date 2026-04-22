# Mesa Conversational Agent

Mesa now supports **real-time Telegram conversations** for prospector screening, not just cron jobs.

## Quick Start

### Run the bot (polling mode)
```bash
cd ~/blackbird_dev/mesa
.venv/bin/python -c "from mesa.cli import main; import sys; sys.argv = ['mesa', 'bot']; main()"
```

Or use the wrapper:
```bash
./run-bot.sh
```

### Or, run a single screening query
```bash
mesa screen under 50
mesa screen between 30 and 80
mesa screen check DDOG NFLX
mesa screen top 5 with IV above 40%
```

## Features

### Natural Language Parsing
The bot recognizes:
- **Intents:** "find", "screen", "show me", "prospects", "what about", "check"
- **Price ranges:** "under $50", "between $30 and $80", "$50 to $100"
- **IV thresholds:** "IV above 40%", "high IV"
- **Specific tickers:** "check DDOG NFLX", "how's JPM"
- **Earnings filter:** "no earnings", "skip earnings"
- **Limits:** "top 5", "best 3"

### Screening Criteria
Each screen returns:
- **Ticker** — Stock symbol
- **Current Price** — Latest close
- **Strike & Expiry** — Put strike + expiration date
- **DTE** — Days to expiration (30-60 day window)
- **Premium** — Annualized return on capital
- **Breakeven** — Downside protection level
- **Cushion** — Margin of safety below current price
- **Open Interest** — Contract liquidity (min 100)
- **Bid-Ask Spread** — Tightness (max 20%)

### Cron Jobs Still Work
The Sunday runs are unchanged:
- **5 PM MT:** Prospector scan (daily opportunities)
- **6 PM MT:** Watchdog summary (position report)

## Architecture

### New Files
- `mesa/conversational.py` — Natural language parsing + screening logic
- `mesa/webhook.py` — Telegram bot handler (polling mode)
- `run-bot.sh` — Service wrapper script
- `mesa-bot.service` — Systemd service file

### Integration
- Uses existing `mesa/telegram.py` for message sending
- Uses existing `yfinance` for market data
- Builds on current watchlist + wheel strategy constraints

## Deployment

### Option 1: Manual (Development)
```bash
./run-bot.sh &
```

### Option 2: Systemd Service (Production)
```bash
# Copy service file
sudo cp mesa-bot.service /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable mesa-bot
sudo systemctl start mesa-bot

# Check status
sudo systemctl status mesa-bot

# View logs
journalctl -u mesa-bot -f
```

### Option 3: Screen/Tmux (Temporary)
```bash
screen -S mesa-bot
./run-bot.sh
# Ctrl+A then D to detach
```

## Customization

### Adjust watchlist
Edit `mesa/conversational.py`:
```python
WATCHLIST = [
    "PLTR", "COIN", "AMD", ...  # Add/remove tickers
]
```

### Adjust screening thresholds
```python
MIN_ANNUALIZED_RETURN = 0.15  # 15% annualized
MIN_OPEN_INTEREST = 100        # Minimum OI
MAX_BID_ASK_SPREAD_PCT = 0.20  # 20% max spread
```

### Add new screening criteria
Edit `parse_criteria()` in `conversational.py`:
```python
# Example: earnings calendar
if "earnings" in msg:
    criteria["exclude_earnings"] = True
```

## Examples

### User: "Find me puts under $60 with high IV"
Bot response:
```
🔭 Prospector Results (3 found)

📌 PLTR $45.23
  Strike: $42 | Exp: 2026-05-15 (37d)
  Premium: $380/contract | Return: 22.4%
  Breakeven: $41.62 | Cushion: 7.8%
  OI: 2,400 | Spread: 8%

[... 2 more results ...]
```

### User: "Check DDOG, how much premium?"
Bot response:
```
🔭 Prospector Results (1 found)

📌 DDOG $65.40
  Strike: $62 | Exp: 2026-05-22 (44d)
  Premium: $450/contract | Return: 18.9%
  Breakeven: $61.30 | Cushion: 6.3%
  OI: 1,850 | Spread: 12%
```

### User: "Top 10 under $50"
Bot scans all tickers under $50, returns top 10 by annualized return.

## Performance

- **Response time:** ~2-5 seconds (depends on yfinance API)
- **Daily limit:** No hard limit (rate-limited by yfinance)
- **Concurrent users:** Polling mode handles single chat ID; can be extended to multi-user with webhook

## Future Enhancements

- [ ] IV rank estimation (Black-Scholes)
- [ ] Earnings calendar integration
- [ ] Portfolio tracking
- [ ] Custom alert thresholds
- [ ] Multi-user support (webhook with auth)
- [ ] SQLite logging of screened opportunities
- [ ] Scheduler for periodic automated screens

## Troubleshooting

### Bot not responding
```bash
# Check service status
systemctl status mesa-bot

# Check logs
tail -f /tmp/mesa-bot.log

# Or run manually
./run-bot.sh
```

### "Telegram not configured"
Verify `.env` has:
```
TELEGRAM_BOT_TOKEN=8797762749:AAFeriwhQtUDN2pWdCSj-2guEiC5pjJ3KJE
TELEGRAM_CHAT_ID=8724336834
```

### yfinance errors
Some tickers may be delisted or have no data. The bot logs and skips them gracefully.

### High latency
yfinance can be slow for large watchlists. Consider:
1. Caching data locally
2. Reducing watchlist size
3. Using a different data provider (Alpha Vantage, etc.)

## Config

All settings in `mesa/config.py`:
- `TELEGRAM_BOT_TOKEN` — Bot token from BotFather
- `TELEGRAM_CHAT_ID` — Your chat ID (where messages go)

Set via environment or `.env` file.
