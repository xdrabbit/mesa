# Mesa

Options monitoring system with two agents:

- **Watchdog** — monitors open positions, alerts on strike/breakeven proximity, DTE warnings, and close opportunities
- **Prospector** — scans a watchlist for attractive cash-secured put opportunities

Alerts are delivered via Telegram bot (`@xdrabbit_bot`).

## Setup

```bash
cd mesa
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Copy `.env.example` to `.env` and fill in your Telegram credentials:

```
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id
```

## Usage

```bash
# Check positions and send alerts
mesa watchdog

# Scan for new opportunities
mesa prospector

# Print current positions
mesa status
```

## Positions

Positions are stored in `positions.json`. Edit directly or add programmatically.

## Cron

See `crontab.example` for recommended schedules:
- Watchdog: every 30 min during market hours
- Prospector: daily at 8 AM ET before market open
