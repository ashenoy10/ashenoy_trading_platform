"""Append-only JSON ledger of monthly cycles. State lives in git."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class CycleRecord:
    period: str  # YYYY-MM
    run_at: str  # ISO timestamp
    income_received: float
    payout: float
    reserve_before: float
    reserve_after: float
    reinvested: float
    principal_sold: float
    principal_value: float
    shares: float
    mode: str  # "live", "paper", or "simulated"
    notes: list[str] = field(default_factory=list)


@dataclass
class Ledger:
    path: Path
    reserve: float = 0.0
    cycles: list[CycleRecord] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "Ledger":
        p = Path(path)
        if not p.exists():
            return cls(path=p)
        raw = json.loads(p.read_text())
        cycles = [CycleRecord(**c) for c in raw.get("cycles", [])]
        return cls(path=p, reserve=float(raw.get("reserve", 0.0)), cycles=cycles)

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"reserve": round(self.reserve, 2), "cycles": [asdict(c) for c in self.cycles]}
        self.path.write_text(json.dumps(payload, indent=2) + "\n")
        return self.path

    def has_period(self, period: str) -> bool:
        return any(c.period == period for c in self.cycles)

    def total_paid(self) -> float:
        return sum(c.payout for c in self.cycles)
