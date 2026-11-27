#!/bin/bash

# Setup MESA Prospector 3x daily cron jobs
# Runs every weekday: 7:45 AM, 12:00 PM, 1:00 PM MT  (= 9:45 AM, 2:00 PM, 3:00 PM ET)
#
# Every slot must fall inside market hours (9:30-16:00 ET = 7:30-14:00 MT): outside them Yahoo
# returns empty bid/ask quotes and the scan can only report "market closed". The first slot
# waits 15 minutes past the open so opening-bell quotes have settled.

set -e

MESA_PATH="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
VENV_PATH="$MESA_PATH/.venv"
LOG_DIR="$MESA_PATH/logs"

echo "╔════════════════════════════════════════════════════════════╗"
echo "║     Setting up MESA Prospector 3x Daily Cron Jobs        ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Create log directory
if [ ! -d "$LOG_DIR" ]; then
  echo "Creating log directory: $LOG_DIR"
  mkdir -p "$LOG_DIR"
fi

# Check venv exists
if [ ! -f "$VENV_PATH/bin/python" ]; then
  echo "❌ Virtual environment not found at $VENV_PATH"
  exit 1
fi

echo "✓ Virtual environment found at $VENV_PATH"
echo "✓ Mesa path: $MESA_PATH"
echo "✓ Log directory: $LOG_DIR"
echo ""

# Create crontab entries for 3x daily scans
# All times in MT timezone
MORNING="45 7 * * 1-5"      # 7:45 AM MT  (9:45 AM ET)
NOON="00 12 * * 1-5"        # 12:00 PM MT (2:00 PM ET)
AFTERNOON="00 13 * * 1-5"   # 1:00 PM MT  (3:00 PM ET — last hour before the close)

# Command template
CMD_TEMPLATE="cd $MESA_PATH && . .venv/bin/activate && python3 -m mesa.cli prospector"

# Full cron entries with logging
CRON_MORNING="$MORNING $CMD_TEMPLATE >> $LOG_DIR/scan-morning.log 2>&1"
CRON_NOON="$NOON $CMD_TEMPLATE >> $LOG_DIR/scan-noon.log 2>&1"
CRON_AFTERNOON="$AFTERNOON $CMD_TEMPLATE >> $LOG_DIR/scan-afternoon.log 2>&1"

echo "Cron jobs to add:"
echo "  1. Morning (7:45 AM MT):    $MORNING"
echo "  2. Noon (12:00 PM MT):      $NOON"
echo "  3. Afternoon (1:00 PM MT):  $AFTERNOON"
echo ""

# Check if any already exist
EXISTING=$(crontab -l 2>/dev/null | grep -c "mesa.cli prospector" || true)
if [ "$EXISTING" -gt 0 ]; then
  echo "⚠️  Found $EXISTING existing prospector cron job(s)"
  echo "Removing old entries before adding new ones..."
  (crontab -l 2>/dev/null | grep -v "mesa.cli prospector") | crontab -
fi

# Add all three cron jobs
(crontab -l 2>/dev/null || echo ""; echo "$CRON_MORNING"; echo "$CRON_NOON"; echo "$CRON_AFTERNOON") | crontab -

echo "✓ Cron jobs added"
echo ""

echo "╔════════════════════════════════════════════════════════════╗"
echo "║                 Setup Complete! 🚀                        ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "View crontab:"
echo "  crontab -l | grep prospector"
echo ""
echo "View logs (live):"
echo "  tail -f $LOG_DIR/scan-*.log"
echo ""
echo "Test immediately (without waiting):"
echo "  cd $MESA_PATH && . .venv/bin/activate && python3 -m mesa.cli prospector"
echo ""
