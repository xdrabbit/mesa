"""CLI entry point for mesa agents.

Usage:
    mesa watchdog              — silent check, alerts only on triggers
    mesa watchdog --summary    — full position report (Sunday)
    mesa prospector            — scan S&P 500 + Nasdaq 100 for cash-secured puts, send to Telegram
    mesa screen <CRITERIA>     — same engine, printed here (e.g. "under 80" "check DDOG" "scan all")
    mesa bot                   — Telegram bot (polling mode; answers TELEGRAM_CHAT_ID only)
    mesa status                — print current positions to stdout
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from mesa.models import load_positions


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(prog="mesa", description="Options monitoring agents")
    parser.add_argument(
        "command",
        choices=["watchdog", "prospector", "screen", "bot", "status"],
        help="Agent to run",
    )
    parser.add_argument(
        "criteria",
        nargs="*",
        help="Screening criteria (for 'screen' command)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Force full report (watchdog summary mode)",
    )
    args = parser.parse_args()

    if args.command == "watchdog":
        from mesa.watchdog import run
        run(summary=args.summary)
    elif args.command == "prospector":
        from mesa.prospector import run
        run()
    elif args.command == "screen":
        if not args.criteria:
            print("Usage: mesa screen <criteria>")
            print("Examples:")
            print("  mesa screen under 80")
            print("  mesa screen between 60 and 90")
            print("  mesa screen check DDOG NFLX")
            print("  mesa screen top 5 high IV")
            print("  mesa screen scan all")
            sys.exit(1)
        from mesa.conversational import respond
        print(respond(" ".join(args.criteria)))
    elif args.command == "bot":
        from mesa.webhook import main as run_bot
        run_bot()
    elif args.command == "status":
        _status()


def _status() -> None:
    positions = load_positions()
    if not positions:
        print("No positions loaded.")
        return

    for p in positions:
        status = "CLOSED" if p.closed else "OPEN"
        print(
            f"[{status}] {p.ticker} ${p.strike} {p.direction.upper()} "
            f"({p.side}) exp {p.expiry} | "
            f"Premium: ${p.premium_collected:.0f} | "
            f"BE: ${p.breakeven:.2f} | "
            f"DTE: {p.days_to_expiry}"
        )


if __name__ == "__main__":
    main()
