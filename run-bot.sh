#!/bin/bash
# Start Mesa Telegram bot
# Usage: ./run-bot.sh
# Logs to: /tmp/mesa-bot.log

cd /home/tom/blackbird_dev/mesa
export PYTHONUNBUFFERED=1

exec .venv/bin/python -c "
from mesa.cli import main
import sys
sys.argv = ['mesa', 'bot']
main()
" >> /tmp/mesa-bot.log 2>&1
