#!/bin/bash

# Setup MESA morning briefing cron job
# Runs every weekday at 7:30 AM MT (9:30 AM ET - market open)

set -e

VENV_PATH="/home/tom/blackbird_dev/mesa/.venv"
SCRIPT_PATH="/home/tom/blackbird_dev/mesa/mesa/morning_brief.py"
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
# 7:30 AM MT = 30 7 * * 1-5 (weekdays only)
CRON_ENTRY="30 7 * * 1-5 source $VENV_PATH/bin/activate && python $SCRIPT_PATH >> $LOG_DIR/morning_brief.log 2>&1"

echo "Adding cron job:"
echo "  Time: 7:30 AM MT (weekdays only)"
echo "  Command: python morning_brief.py"
echo "  Log: $LOG_DIR/morning_brief.log"
echo ""

# Add to crontab (avoid duplicates)
if crontab -l 2>/dev/null | grep -q "morning_brief.py"; then
  echo "⚠️  Cron job already exists. Skipping..."
else
  (crontab -l 2>/dev/null; echo "$CRON_ENTRY") | crontab -
  echo "✓ Cron job added"
fi

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
echo "  source $VENV_PATH/bin/activate && python $SCRIPT_PATH"
echo ""
