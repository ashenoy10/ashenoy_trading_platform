"""Hard risk limits and the monthly profit governor.

Two separate jobs, both of which can halt trading:

RiskManager stops losses. On $3,000 the realistic failure mode is a bad
streak compounding into a dead account, so the limits are checked before
every entry and after every fill.

ProfitGovernor stops *winning*. The stated goal is $500 a month, no more.
Once the month's realized net profit reaches the target, trading halts until
the next month. This is not a nicety: it caps the exposure window, which is
the single most effective risk control available here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from hft.config import Config, RiskLimits


class HaltReason(str):
    pass


@dataclass
class Decision:
    allowed: bool
    reason: str = ""


@dataclass
class RiskManager:
    limits: RiskLimits
    daily_pnl: float = 0.0
    monthly_pnl: float = 0.0
    consecutive_losses: int = 0
    trades_today: int = 0
    equity: float = 0.0
    halted: bool = False
    halt_reason: str = ""
    _day: date | None = None

    def start_day(self, day: date, equity: float) -> None:
        if self._day != day:
            self._day = day
            self.daily_pnl = 0.0
            self.trades_today = 0
            # A new day clears a daily halt but never an equity or monthly halt.
            if self.halted and self.halt_reason.startswith("daily"):
                self.halted = False
                self.halt_reason = ""
        self.equity = equity

    def can_enter(self, notional: float) -> Decision:
        if self.halted:
            return Decision(False, self.halt_reason)
        if self.equity and self.equity <= self.limits.min_equity:
            return self._halt(f"equity ${self.equity:,.2f} at or below floor "
                              f"${self.limits.min_equity:,.2f}")
        if self.daily_pnl <= -self.limits.max_daily_loss:
            return self._halt(f"daily loss limit ${self.limits.max_daily_loss:,.2f} hit")
        if self.monthly_pnl <= -self.limits.max_monthly_loss:
            return self._halt(f"monthly loss limit ${self.limits.max_monthly_loss:,.2f} hit")
        if self.consecutive_losses >= self.limits.max_consecutive_losses:
            return self._halt(f"{self.consecutive_losses} consecutive losses")
        if self.trades_today >= self.limits.max_trades_per_day:
            return Decision(False, "daily trade cap reached")
        if notional > self.limits.max_position_notional:
            return Decision(False, f"notional ${notional:,.2f} exceeds position cap")
        return Decision(True)

    def _halt(self, reason: str) -> Decision:
        self.halted = True
        self.halt_reason = reason
        return Decision(False, reason)

    def record_trade(self, pnl: float) -> None:
        self.daily_pnl += pnl
        self.monthly_pnl += pnl
        self.equity += pnl
        self.trades_today += 1
        self.consecutive_losses = self.consecutive_losses + 1 if pnl < 0 else 0

    def start_month(self) -> None:
        self.monthly_pnl = 0.0
        self.consecutive_losses = 0
        if self.halted and self.halt_reason.startswith(("monthly", "daily")):
            self.halted = False
            self.halt_reason = ""


@dataclass
class ProfitGovernor:
    """Stops trading for the month once the target is reached."""

    target: float
    enabled: bool = True
    realized: float = 0.0
    reached_on: str | None = None

    def record(self, pnl: float, when: str | None = None) -> None:
        self.realized += pnl
        if self.enabled and self.reached_on is None and self.realized >= self.target:
            self.reached_on = when or "unknown"

    def can_trade(self) -> Decision:
        if not self.enabled:
            return Decision(True)
        if self.realized >= self.target:
            return Decision(False, f"monthly target ${self.target:,.2f} reached "
                                   f"(realized ${self.realized:,.2f})")
        return Decision(True)

    @property
    def remaining(self) -> float:
        return max(self.target - self.realized, 0.0)

    def reset(self) -> None:
        self.realized = 0.0
        self.reached_on = None


def position_notional(cfg: Config, equity: float, remaining_to_target: float,
                      price: float, stop_distance_bps: float) -> float:
    """Size a position from the risk budget, then cap it.

    Two caps apply, smaller wins:
      1. risk budget: max_trade_risk / stop distance
      2. position cap: max_position_notional, which defaults to `capital`
         (no leverage) and is the only place leverage is expressed

    Equity is deliberately NOT a third cap. Capping at raw equity would make
    max_position_notional unreachable above 1x and silently ignore any
    leverage setting. Equity protection is the risk manager's job, through
    the equity floor and the daily and monthly loss limits.
    """
    if stop_distance_bps <= 0 or price <= 0 or equity <= 0:
        return 0.0
    risk_sized = cfg.risk.max_trade_risk / (stop_distance_bps / 10_000.0)
    return max(min(risk_sized, cfg.risk.max_position_notional), 0.0)
