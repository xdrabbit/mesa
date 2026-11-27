#!/bin/bash

# Setup MESA morning briefing cron job
# Runs every weekday at 7:45 AM MT (9:45 AM ET). Must be inside market hours: outside them Yahoo
# returns empty option quotes. 15 min past the open lets opening-bell quotes settle.

set -e

MESA_PATH="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
VENV_PATH="$MESA_PATH/.venv"
SCRIPT_PATH="$MESA_PATH/mesa/morning_brief.py"
LOG_DIR="/var/log/mesa"

echo "╔════════════════════════════════════════════════════════════╗"
echo "║        Setting up MESA Morning Briefing Cron Job          ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Create log directory
if [ ! -d "$LOG_DIR" ]; then
  echo "Creating log directory: $LOG_DIR"
  mkdir -p "$LOG_DIR" 2>/dev/null || {
    echo "⚠️  Cannot create $LOG_DIR (needs sudo)"
    LOG_DIR="/tmp/mesa-logs"
    mkdir -p "$LOG_DIR"
    echo "Using $LOG_DIR instead"
  }
fi

# Check venv exists
if [ ! -f "$VENV_PATH/bin/python" ]; then
  echo "❌ Virtual environment not found at $VENV_PATH"
  exit 1
fi

echo "✓ Virtual environment found"
echo "✓ Script path: $SCRIPT_PATH"
echo ""

# Create crontab entry
# 7:45 AM MT = 45 7 * * 1-5 (weekdays only)
# Call the venv's python directly: cron runs /bin/sh, which on Debian/Ubuntu is dash and has no
# `source`, so the old "source .../activate && python ..." entry silently never ran.
CRON_ENTRY="45 7 * * 1-5 $VENV_PATH/bin/python $SCRIPT_PATH >> $LOG_DIR/morning_brief.log 2>&1"

echo "Adding cron job:"
echo "  Time: 7:45 AM MT (weekdays only)"
echo "  Command: python morning_brief.py"
echo "  Log: $LOG_DIR/morning_brief.log"
echo ""

# Replace any previous entry (an older one may use the broken `source` form)
if crontab -l 2>/dev/null | grep -q "morning_brief.py"; then
  echo "Replacing existing morning_brief cron entry..."
fi
(crontab -l 2>/dev/null | grep -v "morning_brief.py"; echo "$CRON_ENTRY") | crontab -
echo "✓ Cron job added"

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║                 Setup Complete! 🌅                        ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "View crontab:"
echo "  crontab -l"
echo ""
echo "View logs:"
echo "  tail -f $LOG_DIR/morning_brief.log"
echo ""
echo "Test immediately (without waiting):"
echo "  $VENV_PATH/bin/python $SCRIPT_PATH"
echo ""
