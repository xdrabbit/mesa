from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Literal

from mesa.config import POSITIONS_FILE


@dataclass
class Position:
    ticker: str
    strike: float
    expiry: str  # YYYY-MM-DD
    direction: Literal["put", "call"]
    side: Literal["short", "long"]
    premium_collected: float
    contracts: int = 1
    closed: bool = False

    @property
    def breakeven(self) -> float:
        if self.direction == "put" and self.side == "short":
            return self.strike - (self.premium_collected / (100 * self.contracts))
        if self.direction == "call" and self.side == "short":
            return self.strike + (self.premium_collected / (100 * self.contracts))
        return self.strike

    @property
    def expiry_date(self) -> date:
        return date.fromisoformat(self.expiry)

    @property
    def days_to_expiry(self) -> int:
        return (self.expiry_date - date.today()).days


def load_positions() -> list[Position]:
    if not POSITIONS_FILE.exists():
        return []
    data = json.loads(POSITIONS_FILE.read_text())
    return [Position(**p) for p in data]


def save_positions(positions: list[Position]) -> None:
    POSITIONS_FILE.write_text(json.dumps([asdict(p) for p in positions], indent=2))
