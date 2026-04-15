"""Watchdog agent — monitors open positions and alerts on key levels.

Alerts:
  - Price within 5% of strike
  - Price within 5% of breakeven
  - Price breaches strike or breakeven
  - < 14 days to expiry
  - Position is profitable to close (current option premium < 20% of collected)
"""
from __future__ import annotations

import logging
from datetime import date

from mesa.models import Position, load_positions
from mesa.market import get_price, get_option_chain
from mesa.telegram import send

log = logging.getLogger(__name__)

PROXIMITY_PCT = 0.05  # 5%
DTE_WARNING = 14
CLOSE_THRESHOLD = 0.20  # close when option worth < 20% of premium collected


def run() -> None:
    positions = [p for p in load_positions() if not p.closed]
    if not positions:
        log.info("No open positions to watch")
        return

    alerts: list[str] = []
    for pos in positions:
        try:
            alerts.extend(_check(pos))
        except Exception as e:
            log.error("Error checking %s: %s", pos.ticker, e)
            alerts.append(f"⚠️ Error checking {pos.ticker}: {e}")

    if alerts:
        header = f"🐕 *Watchdog Report* — {date.today()}"
        send(header + "\n\n" + "\n\n".join(alerts))
    else:
        log.info("All positions nominal — no alerts")


def _check(pos: Position) -> list[str]:
    alerts: list[str] = []
    price = get_price(pos.ticker)
    if price is None:
        return [f"⚠️ Could not fetch price for {pos.ticker}"]

    label = (
        f"{pos.ticker} ${pos.strike} {pos.direction.upper()} "
        f"({pos.expiry}, {pos.side})"
    )

    # --- Price vs strike ---
    dist_strike = abs(price - pos.strike) / pos.strike
    if price <= pos.strike and pos.direction == "put" and pos.side == "short":
        alerts.append(
            f"🔴 *{label}*\n"
            f"Price ${price:.2f} is *below strike* ${pos.strike:.2f}\n"
            f"Position is ITM — assignment risk"
        )
    elif dist_strike < PROXIMITY_PCT:
        alerts.append(
            f"🟡 *{label}*\n"
            f"Price ${price:.2f} is within {dist_strike:.1%} of strike ${pos.strike:.2f}"
        )

    # --- Price vs breakeven ---
    be = pos.breakeven
    dist_be = abs(price - be) / be
    if price <= be and pos.direction == "put" and pos.side == "short":
        alerts.append(
            f"🔴 *{label}*\n"
            f"Price ${price:.2f} *below breakeven* ${be:.2f}\n"
            f"Position is a net loss at expiry"
        )
    elif dist_be < PROXIMITY_PCT:
        alerts.append(
            f"🟡 *{label}*\n"
            f"Price ${price:.2f} is within {dist_be:.1%} of breakeven ${be:.2f}"
        )

    # --- DTE warning ---
    dte = pos.days_to_expiry
    if 0 < dte <= DTE_WARNING:
        alerts.append(
            f"⏰ *{label}*\n"
            f"Only *{dte} days* to expiry"
        )
    elif dte <= 0:
        alerts.append(f"🏁 *{label}*\nPosition has *expired*")

    # --- Close opportunity (check current option price) ---
    chain = get_option_chain(pos.ticker, pos.expiry)
    if chain:
        df = chain["puts"] if pos.direction == "put" else chain["calls"]
        row = df[df["strike"] == pos.strike]
        if not row.empty:
            mid = (float(row["bid"].iloc[0]) + float(row["ask"].iloc[0])) / 2
            current_value = mid * 100 * pos.contracts
            if current_value < pos.premium_collected * CLOSE_THRESHOLD:
                alerts.append(
                    f"💰 *{label}*\n"
                    f"Option worth ~${current_value:.0f} "
                    f"({current_value / pos.premium_collected:.0%} of ${pos.premium_collected:.0f} collected)\n"
                    f"Consider closing for profit"
                )

    # --- Summary line if no alerts ---
    if not alerts:
        alerts.append(
            f"✅ *{label}*\n"
            f"Price ${price:.2f} | BE ${be:.2f} | DTE {dte}"
        )

    return alerts
