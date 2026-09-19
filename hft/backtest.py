"""Event-driven backtest with full cost accounting.

Every fill pays the spread, slippage and regulatory fees from hft.costs. The
risk limits and the profit governor are active, so the backtest exercises the
same control path as live trading rather than an idealised version of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from hft.config import Config
from hft.costs import one_side_cost
from hft.risk import ProfitGovernor, RiskManager, position_notional
from hft.strategy import Action, Bar, ScalpStrategy, Side


@dataclass
class Fill:
    ts: object
    symbol: str
    side: str
    notional: float
    price: float
    cost: float


@dataclass
class ClosedTrade:
    symbol: str
    side: str
    entry_ts: object
    exit_ts: object
    entry_price: float
    exit_price: float
    notional: float
    gross_pnl: float
    costs: float
    reason: str

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.costs


@dataclass
class BacktestResult:
    trades: list[ClosedTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    halts: list[str] = field(default_factory=list)
    target_reached_on: str | None = None
    bars_processed: int = 0

    @property
    def net_pnl(self) -> float:
        return sum(t.net_pnl for t in self.trades)

    @property
    def gross_pnl(self) -> float:
        return sum(t.gross_pnl for t in self.trades)

    @property
    def total_costs(self) -> float:
        return sum(t.costs for t in self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return sum(1 for t in self.trades if t.net_pnl > 0) / len(self.trades)

    @property
    def max_drawdown(self) -> float:
        peak = float("-inf")
        worst = 0.0
        for eq in self.equity_curve:
            peak = max(peak, eq)
            worst = min(worst, eq - peak)
        return worst

    def summary(self, capital: float) -> dict:
        n = len(self.trades)
        avg_bps = 0.0
        if n:
            avg_bps = sum(t.net_pnl / t.notional for t in self.trades) / n * 10_000.0
        return {
            "bars_processed": self.bars_processed,
            "trades": n,
            "gross_pnl": round(self.gross_pnl, 2),
            "total_costs": round(self.total_costs, 2),
            "net_pnl": round(self.net_pnl, 2),
            "avg_net_bps_per_trade": round(avg_bps, 3),
            "win_rate": round(self.win_rate, 4),
            "max_drawdown": round(self.max_drawdown, 2),
            "return_on_capital": round(self.net_pnl / capital, 4) if capital else 0.0,
            "target_reached_on": self.target_reached_on,
            "halts": self.halts[:5],
        }


class Backtester:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def run(self, bars: Iterable[Bar]) -> BacktestResult:
        cfg = self.cfg
        strat = ScalpStrategy(cfg.strategy)
        risk = RiskManager(limits=cfg.risk, equity=cfg.capital)
        gov = ProfitGovernor(target=cfg.monthly_target, enabled=cfg.stop_at_target)
        res = BacktestResult()

        equity = cfg.capital
        cur_month: tuple[int, int] | None = None
        open_pos: dict[str, dict] = {}
        # Stop distance drives sizing: the z-gap between entry and stop,
        # expressed in bps, using the strategy's own volatility estimate.
        stop_gap_z = max(cfg.strategy.stop_z - cfg.strategy.entry_z, 0.5)

        for bar in bars:
            res.bars_processed += 1
            day = bar.ts.date() if hasattr(bar.ts, "date") else None
            if day is not None:
                if cur_month is not None and (day.year, day.month) != cur_month:
                    # New calendar month: monthly loss budget and the profit
                    # governor both reset, exactly as they do in live trading.
                    risk.start_month()
                    gov.reset()
                cur_month = (day.year, day.month)
                risk.start_day(day, equity)

            sig = strat.on_bar(bar)
            pos = open_pos.get(bar.symbol)

            if pos and sig.action is Action.EXIT:
                exit_cost = one_side_cost(
                    cfg.costs, pos["notional"], pos["shares"],
                    is_sell=(pos["side"] == Side.LONG),
                ).total
                direction = 1.0 if pos["side"] is Side.LONG else -1.0
                gross = direction * (bar.close - pos["price"]) / pos["price"] * pos["notional"]
                costs = pos["entry_cost"] + exit_cost
                trade = ClosedTrade(
                    symbol=bar.symbol, side=pos["side"].value,
                    entry_ts=pos["ts"], exit_ts=bar.ts,
                    entry_price=pos["price"], exit_price=bar.close,
                    notional=pos["notional"], gross_pnl=gross, costs=costs,
                    reason=sig.reason,
                )
                res.trades.append(trade)
                equity += trade.net_pnl
                risk.record_trade(trade.net_pnl)
                gov.record(trade.net_pnl, str(bar.ts))
                if gov.reached_on and res.target_reached_on is None:
                    res.target_reached_on = gov.reached_on
                res.equity_curve.append(equity)
                strat.mark_exit(bar.symbol)
                open_pos.pop(bar.symbol, None)
                continue

            if pos or sig.action not in (Action.ENTER_LONG, Action.ENTER_SHORT):
                continue

            gate = gov.can_trade()
            if not gate.allowed:
                if gate.reason not in res.halts:
                    res.halts.append(gate.reason)
                continue

            # Convert the z-distance to the stop into a bps distance.
            window = list(strat._st(bar.symbol).prices)[-cfg.strategy.lookback:]
            mean = sum(window) / len(window)
            sd = (sum((x - mean) ** 2 for x in window) / (len(window) - 1)) ** 0.5
            stop_bps = (stop_gap_z * sd) / bar.close * 10_000.0
            if stop_bps <= 0:
                continue

            notional = position_notional(cfg, equity, gov.remaining, bar.close, stop_bps)
            decision = risk.can_enter(notional)
            if not decision.allowed:
                if decision.reason not in res.halts:
                    res.halts.append(decision.reason)
                continue
            if notional < 1.0:
                continue

            side = Side.LONG if sig.action is Action.ENTER_LONG else Side.SHORT
            shares = notional / bar.close
            entry_cost = one_side_cost(
                cfg.costs, notional, shares, is_sell=(side is Side.SHORT)
            ).total
            open_pos[bar.symbol] = {
                "side": side, "price": bar.close, "notional": notional,
                "shares": shares, "entry_cost": entry_cost, "ts": bar.ts,
            }
            strat.mark_entry(bar.symbol, side, bar.close, sig.z)

        return res
