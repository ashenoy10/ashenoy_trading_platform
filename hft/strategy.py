"""Short-horizon mean reversion scalper.

Signal: rolling z-score of price against its own recent mean. Fade extremes,
exit on reversion, stop on continuation, and force an exit after a fixed
number of bars so capital is never parked in a losing idea.

Deliberately few parameters. With $3,000 and a monthly target, the temptation
is to keep adding conditions until the backtest looks good; that produces a
curve-fit artifact that dies in live trading. Five parameters is already
enough rope.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import time as dtime
from enum import Enum
from zoneinfo import ZoneInfo

from hft.config import StrategyParams


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class Action(str, Enum):
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"
    EXIT = "exit"
    HOLD = "hold"


@dataclass
class Bar:
    ts: object          # datetime
    symbol: str
    close: float
    volume: float = 0.0


@dataclass
class Signal:
    action: Action
    reason: str
    z: float


@dataclass
class SymbolState:
    prices: deque = field(default_factory=lambda: deque(maxlen=512))
    side: Side = Side.FLAT
    bars_held: int = 0
    cooldown: int = 0
    entry_price: float = 0.0
    entry_z: float = 0.0


MARKET_TZ = ZoneInfo("America/New_York")


def _parse_hhmm(value: str) -> dtime:
    h, m = value.split(":")
    return dtime(int(h), int(m))


class ScalpStrategy:
    def __init__(self, params: StrategyParams | None = None):
        self.p = params or StrategyParams()
        self.state: dict[str, SymbolState] = {}
        self._start = _parse_hhmm(self.p.trade_start)
        self._end = _parse_hhmm(self.p.trade_end)

    def _st(self, symbol: str) -> SymbolState:
        return self.state.setdefault(symbol, SymbolState())

    def zscore(self, symbol: str) -> float | None:
        st = self._st(symbol)
        n = self.p.lookback
        if len(st.prices) < n:
            return None
        window = list(st.prices)[-n:]
        mean = sum(window) / n
        var = sum((x - mean) ** 2 for x in window) / (n - 1)
        sd = var ** 0.5
        if sd <= 1e-12:
            return None
        return (window[-1] - mean) / sd

    def in_session(self, ts) -> bool:
        """Session bounds are US market local time, not UTC.

        Alpaca returns bar timestamps in UTC. Comparing those directly against
        an Eastern window silently shifted the trading day by 4-5 hours and put
        it in the pre-market, which is exactly what the first real-data run hit.
        """
        if hasattr(ts, "tzinfo"):
            t = (ts.astimezone(MARKET_TZ) if ts.tzinfo is not None else ts).time()
        else:
            t = ts
        return self._start <= t <= self._end

    def on_bar(self, bar: Bar) -> Signal:
        st = self._st(bar.symbol)
        st.prices.append(bar.close)
        if st.cooldown > 0:
            st.cooldown -= 1

        z = self.zscore(bar.symbol)
        if z is None:
            return Signal(Action.HOLD, "warming up", 0.0)

        # Flatten everything before the close regardless of position state.
        if not self.in_session(bar.ts):
            if st.side is not Side.FLAT:
                return Signal(Action.EXIT, "outside trading session", z)
            return Signal(Action.HOLD, "outside trading session", z)

        if st.side is not Side.FLAT:
            st.bars_held += 1
            if st.side is Side.LONG:
                if z >= -self.p.exit_z:
                    return Signal(Action.EXIT, "reverted to mean", z)
                if z <= -self.p.stop_z:
                    return Signal(Action.EXIT, "stop: continued against position", z)
            else:
                if z <= self.p.exit_z:
                    return Signal(Action.EXIT, "reverted to mean", z)
                if z >= self.p.stop_z:
                    return Signal(Action.EXIT, "stop: continued against position", z)
            if st.bars_held >= self.p.hold_limit:
                return Signal(Action.EXIT, "hold limit reached", z)
            return Signal(Action.HOLD, "holding", z)

        if st.cooldown > 0:
            return Signal(Action.HOLD, "cooldown", z)
        # Do not open a position we would immediately have to flatten.
        if z <= -self.p.entry_z:
            return Signal(Action.ENTER_LONG, f"z={z:.2f} below -{self.p.entry_z}", z)
        if z >= self.p.entry_z:
            return Signal(Action.ENTER_SHORT, f"z={z:.2f} above {self.p.entry_z}", z)
        return Signal(Action.HOLD, "no signal", z)

    # -- position bookkeeping, called by the executor after a fill ---------
    def mark_entry(self, symbol: str, side: Side, price: float, z: float) -> None:
        st = self._st(symbol)
        st.side = side
        st.bars_held = 0
        st.entry_price = price
        st.entry_z = z

    def mark_exit(self, symbol: str) -> None:
        st = self._st(symbol)
        st.side = Side.FLAT
        st.bars_held = 0
        st.entry_price = 0.0
        st.cooldown = self.p.cooldown_bars
