"""Live/paper trading loop.

Polls 1-minute bars, runs the strategy, and routes orders through the risk
manager and the profit governor. Every closed trade is persisted immediately,
so a crash mid-session loses at most the open position's bookkeeping.

This is a minute-bar loop, not a microsecond one. It is built for the latency
a retail REST API actually delivers. See README for why that matters.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from hft.broker import AlpacaBroker, PaperBroker
from hft.config import Config
from hft.costs import one_side_cost
from hft.report import write_report
from hft.risk import ProfitGovernor, RiskManager, position_notional
from hft.state import MonthState, TradeLog
from hft.strategy import Action, Bar, ScalpStrategy, Side

LOG = logging.getLogger("hft.runner")


class Runner:
    def __init__(self, cfg: Config, broker=None, feed=None):
        self.cfg = cfg
        self.broker = broker or PaperBroker()
        self.feed = feed
        self.strategy = ScalpStrategy(cfg.strategy)
        period = datetime.now(timezone.utc).strftime("%Y-%m")
        self.state = MonthState.load(cfg.state_path, period, cfg.capital)
        self.risk = RiskManager(limits=cfg.risk, equity=self.state.equity or cfg.capital)
        self.risk.monthly_pnl = self.state.realized
        self.gov = ProfitGovernor(target=cfg.monthly_target, enabled=cfg.stop_at_target)
        self.gov.realized = self.state.realized
        self.gov.reached_on = self.state.target_reached_on
        self.open_pos: dict[str, dict] = {}

    # -- one bar ----------------------------------------------------------
    def on_bar(self, bar: Bar) -> None:
        cfg = self.cfg
        day = bar.ts.date() if hasattr(bar.ts, "date") else None
        if day is not None:
            self.risk.start_day(day, self.state.equity)

        sig = self.strategy.on_bar(bar)
        pos = self.open_pos.get(bar.symbol)

        if pos and sig.action is Action.EXIT:
            self._close(bar, pos, sig.reason)
            return
        if pos or sig.action not in (Action.ENTER_LONG, Action.ENTER_SHORT):
            return

        gate = self.gov.can_trade()
        if not gate.allowed:
            LOG.info("not trading: %s", gate.reason)
            return

        window = list(self.strategy._st(bar.symbol).prices)[-cfg.strategy.lookback:]
        if len(window) < 2:
            return
        mean = sum(window) / len(window)
        sd = (sum((x - mean) ** 2 for x in window) / (len(window) - 1)) ** 0.5
        stop_gap_z = max(cfg.strategy.stop_z - cfg.strategy.entry_z, 0.5)
        stop_bps = (stop_gap_z * sd) / bar.close * 10_000.0
        if stop_bps <= 0:
            return

        notional = position_notional(cfg, self.state.equity, self.gov.remaining,
                                     bar.close, stop_bps)
        decision = self.risk.can_enter(notional)
        if not decision.allowed:
            LOG.warning("entry blocked: %s", decision.reason)
            self.state.halted = self.risk.halted
            self.state.halt_reason = self.risk.halt_reason
            self.state.save()
            return
        if notional < 1.0:
            return

        side = Side.LONG if sig.action is Action.ENTER_LONG else Side.SHORT
        order_side = "buy" if side is Side.LONG else "sell"
        try:
            self.broker.submit(bar.symbol, round(notional, 2), order_side)
        except PermissionError as e:
            LOG.error("order refused: %s", e)
            return

        shares = notional / bar.close
        self.open_pos[bar.symbol] = {
            "side": side, "price": bar.close, "notional": notional, "shares": shares,
            "entry_cost": one_side_cost(cfg.costs, notional, shares,
                                        is_sell=(side is Side.SHORT)).total,
            "ts": bar.ts,
        }
        self.strategy.mark_entry(bar.symbol, side, bar.close, sig.z)
        LOG.info("entered %s %s $%.2f @ %.4f (%s)", side.value, bar.symbol,
                 notional, bar.close, sig.reason)

    def _close(self, bar: Bar, pos: dict, reason: str) -> None:
        cfg = self.cfg
        order_side = "sell" if pos["side"] is Side.LONG else "buy"
        try:
            self.broker.submit(bar.symbol, round(pos["notional"], 2), order_side)
        except PermissionError as e:
            LOG.error("exit refused: %s", e)
            return

        exit_cost = one_side_cost(cfg.costs, pos["notional"], pos["shares"],
                                  is_sell=(pos["side"] is Side.LONG)).total
        direction = 1.0 if pos["side"] is Side.LONG else -1.0
        gross = direction * (bar.close - pos["price"]) / pos["price"] * pos["notional"]
        costs = pos["entry_cost"] + exit_cost
        net = gross - costs

        self.state.trades.append(TradeLog(
            ts=str(bar.ts), symbol=bar.symbol, side=pos["side"].value,
            notional=round(pos["notional"], 2), entry=pos["price"], exit=bar.close,
            gross=round(gross, 4), costs=round(costs, 4), net=round(net, 4),
            reason=reason,
        ))
        self.state.realized += net
        self.state.equity += net
        self.risk.record_trade(net)
        self.gov.record(net, str(bar.ts))
        self.state.target_reached_on = self.gov.reached_on
        self.state.halted = self.risk.halted
        self.state.halt_reason = self.risk.halt_reason
        self.state.save()
        self.strategy.mark_exit(bar.symbol)
        self.open_pos.pop(bar.symbol, None)
        LOG.info("closed %s %s net $%+.2f (%s) | month $%.2f",
                 pos["side"].value, bar.symbol, net, reason, self.state.realized)

    # -- session ----------------------------------------------------------
    def run_bars(self, bars) -> MonthState:
        for bar in bars:
            self.on_bar(bar)
            if not self.gov.can_trade().allowed and not self.open_pos:
                LOG.info("monthly target reached; stopping")
                break
        self.state.save()
        write_report(self.cfg, self.state)
        return self.state

    def run_live(self, poll_seconds: int = 60, max_minutes: int | None = None) -> MonthState:
        if self.feed is None:
            raise ValueError("run_live needs a market data feed")
        seen: dict[str, object] = {}
        started = time.time()
        while True:
            if max_minutes and (time.time() - started) / 60.0 >= max_minutes:
                break
            if not self.gov.can_trade().allowed and not self.open_pos:
                LOG.info("monthly target reached; stopping")
                break
            for symbol in self.cfg.strategy.symbols:
                try:
                    bars = self.feed.history(
                        symbol,
                        start=(datetime.now(timezone.utc).date()).isoformat(),
                        end=datetime.now(timezone.utc).isoformat(),
                    )
                except Exception as e:
                    LOG.error("data fetch failed for %s: %s", symbol, e)
                    continue
                for bar in bars:
                    if seen.get(symbol) and bar.ts <= seen[symbol]:
                        continue
                    seen[symbol] = bar.ts
                    self.on_bar(bar)
            time.sleep(poll_seconds)
        self.state.save()
        write_report(self.cfg, self.state)
        return self.state


def build_runner(cfg: Config | None = None) -> Runner:
    import os
    cfg = cfg or Config.from_env()
    if os.getenv("USE_ALPACA", "").lower() == "true":
        from hft.marketdata import AlpacaBars
        return Runner(cfg, broker=AlpacaBroker(), feed=AlpacaBars())
    return Runner(cfg, broker=PaperBroker())
