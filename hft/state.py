"""Persistent month state, committed to git so every run is auditable."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class TradeLog:
    ts: str
    symbol: str
    side: str
    notional: float
    entry: float
    exit: float
    gross: float
    costs: float
    net: float
    reason: str


@dataclass
class MonthState:
    path: Path
    period: str = ""
    realized: float = 0.0
    trades: list[TradeLog] = field(default_factory=list)
    halted: bool = False
    halt_reason: str = ""
    target_reached_on: str | None = None
    equity: float = 0.0

    @classmethod
    def load(cls, path: str | Path, period: str, capital: float) -> "MonthState":
        p = Path(path)
        if not p.exists():
            return cls(path=p, period=period, equity=capital)
        raw = json.loads(p.read_text())
        if raw.get("period") != period:
            # New month: reset the P&L but carry the equity forward.
            return cls(path=p, period=period, equity=float(raw.get("equity", capital)))
        return cls(
            path=p, period=raw["period"], realized=float(raw.get("realized", 0.0)),
            trades=[TradeLog(**t) for t in raw.get("trades", [])],
            halted=bool(raw.get("halted", False)),
            halt_reason=raw.get("halt_reason", ""),
            target_reached_on=raw.get("target_reached_on"),
            equity=float(raw.get("equity", capital)),
        )

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "period": self.period,
            "realized": round(self.realized, 2),
            "equity": round(self.equity, 2),
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "target_reached_on": self.target_reached_on,
            "trades": [asdict(t) for t in self.trades],
        }, indent=2) + "\n")
        return self.path
