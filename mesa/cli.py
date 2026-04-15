"""CLI entry point for mesa agents.

Usage:
    mesa watchdog     — check open positions, send alerts
    mesa prospector   — scan for new put-selling opportunities
    mesa status       — print current positions to stdout
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
        choices=["watchdog", "prospector", "status"],
        help="Agent to run",
    )
    args = parser.parse_args()

    if args.command == "watchdog":
        from mesa.watchdog import run
        run()
    elif args.command == "prospector":
        from mesa.prospector import run
        run()
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
