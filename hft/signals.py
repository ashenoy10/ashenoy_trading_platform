"""Candidate intraday strategies.

Each implements the same interface the backtester already drives:
on_bar -> Signal, plus mark_entry / mark_exit. That lets the search compare
very different ideas through one cost-accurate engine.

Design bias, learned the hard way: the z-score scalper failed because it hunted
2-3 bps moves while paying 2.7 bps round trip. Everything here targets moves
an order of magnitude larger, holding minutes to hours, so cost is a fraction
of the edge rather than all of it. That is the only structural way a retail
account beats the spread.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import time as dtime
from zoneinfo import ZoneInfo

from hft.strategy import Action, Bar, Side, Signal

MARKET_TZ = ZoneInfo("America/New_York")


def _hhmm(value: str) -> dtime:
    h, m = value.split(":")
    return dtime(int(h), int(m))


def _et(ts):
    if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
        return ts.astimezone(MARKET_TZ)
    return ts


@dataclass
class _DayState:
    day: object = None
    or_high: float = 0.0
    or_low: float = 0.0
    or_done: bool = False
    bars_seen: int = 0
    traded: bool = False
    side: Side = Side.FLAT
    entry_price: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    vwap_num: float = 0.0
    vwap_den: float = 0.0
    closes: deque = field(default_factory=lambda: deque(maxlen=512))


class OpeningRangeBreakout:
    """Trade the first decisive break of the opening range.

    The opening range is the high and low of the first `or_minutes` of regular
    trading. A close beyond it signals the day's direction. Stop goes at the
    opposite edge of the range, target is a multiple of the range width, and
    anything still open is flattened before the close.

    One trade per symbol per day. Deliberately low frequency: the edge being
    harvested is a multi-hour directional move, which is large relative to
    cost, and taking only the first signal avoids paying the spread repeatedly
    on a choppy day.
    """

    name = "orb"

    def __init__(self, or_minutes: int = 15, stop_mult: float = 1.0,
                 target_mult: float = 2.0, session_start: str = "09:30",
                 session_end: str = "15:50", min_range_bps: float = 5.0):
        self.or_minutes = or_minutes
        self.stop_mult = stop_mult
        self.target_mult = target_mult
        self.start = _hhmm(session_start)
        self.end = _hhmm(session_end)
        self.min_range_bps = min_range_bps
        self.state: dict[str, _DayState] = {}

    def params(self) -> dict:
        return {"or_minutes": self.or_minutes, "stop_mult": self.stop_mult,
                "target_mult": self.target_mult, "min_range_bps": self.min_range_bps}

    def _st(self, sym: str) -> _DayState:
        return self.state.setdefault(sym, _DayState())

    def on_bar(self, bar: Bar) -> Signal:
        st = self._st(bar.symbol)
        t = _et(bar.ts)
        day = t.date()

        if st.day != day:
            was_open = st.side is not Side.FLAT
            st.day = day
            st.or_high = st.or_low = 0.0
            st.or_done = False
            st.bars_seen = 0
            st.traded = False
            if was_open:
                st.side = Side.FLAT
                return Signal(Action.EXIT, "new session, flattening", 0.0)
            st.side = Side.FLAT

        clock = t.time()
        if clock < self.start or clock > self.end:
            if st.side is not Side.FLAT:
                return Signal(Action.EXIT, "session end", 0.0)
            return Signal(Action.HOLD, "outside session", 0.0)

        # Build the opening range.
        if not st.or_done:
            st.bars_seen += 1
            st.or_high = max(st.or_high, bar.high) if st.or_high else bar.high
            st.or_low = min(st.or_low, bar.low) if st.or_low else bar.low
            if st.bars_seen >= self.or_minutes:
                st.or_done = True
            return Signal(Action.HOLD, "building opening range", 0.0)

        width = st.or_high - st.or_low
        if width <= 0:
            return Signal(Action.HOLD, "degenerate range", 0.0)

        if st.side is not Side.FLAT:
            if st.side is Side.LONG:
                if bar.low <= st.stop:
                    return Signal(Action.EXIT, "stop hit", 0.0)
                if bar.high >= st.target:
                    return Signal(Action.EXIT, "target hit", 0.0)
            else:
                if bar.high >= st.stop:
                    return Signal(Action.EXIT, "stop hit", 0.0)
                if bar.low <= st.target:
                    return Signal(Action.EXIT, "target hit", 0.0)
            return Signal(Action.HOLD, "holding", 0.0)

        if st.traded:
            return Signal(Action.HOLD, "already traded today", 0.0)

        # A range too tight to cover costs is not worth trading.
        if width / bar.close * 10_000.0 < self.min_range_bps:
            return Signal(Action.HOLD, "opening range too tight", 0.0)

        if bar.close > st.or_high:
            st.stop = st.or_high - width * self.stop_mult
            st.target = st.or_high + width * self.target_mult
            return Signal(Action.ENTER_LONG, f"broke above {st.or_high:.2f}", 0.0)
        if bar.close < st.or_low:
            st.stop = st.or_low + width * self.stop_mult
            st.target = st.or_low - width * self.target_mult
            return Signal(Action.ENTER_SHORT, f"broke below {st.or_low:.2f}", 0.0)
        return Signal(Action.HOLD, "inside range", 0.0)

    def stop_bps_for(self, bar: Bar) -> float:
        st = self._st(bar.symbol)
        width = st.or_high - st.or_low
        if width <= 0 or bar.close <= 0:
            return 0.0
        return width * self.stop_mult / bar.close * 10_000.0

    def mark_entry(self, symbol: str, side: Side, price: float, z: float) -> None:
        st = self._st(symbol)
        st.side = side
        st.entry_price = price
        st.traded = True

    def mark_exit(self, symbol: str) -> None:
        self._st(symbol).side = Side.FLAT


class VWAPReversion:
    """Fade stretched moves away from the session VWAP.

    VWAP is the day's volume-weighted average price, the level institutional
    flow is measured against. Price far above or below it tends to be pulled
    back. Entry is a deviation of `entry_bps` from VWAP, exit is a return to
    it, with a hard stop and a time limit.

    Larger threshold than the z-score scalper by design: `entry_bps` is tens of
    basis points, not single digits.
    """

    name = "vwap_reversion"

    def __init__(self, entry_bps: float = 40.0, stop_bps: float = 60.0,
                 hold_limit: int = 120, session_start: str = "09:45",
                 session_end: str = "15:50", warmup: int = 30):
        self.entry_bps = entry_bps
        self.stop_bps = stop_bps
        self.hold_limit = hold_limit
        self.start = _hhmm(session_start)
        self.end = _hhmm(session_end)
        self.warmup = warmup
        self.state: dict[str, _DayState] = {}
        self._held: dict[str, int] = {}

    def params(self) -> dict:
        return {"entry_bps": self.entry_bps, "stop_bps": self.stop_bps,
                "hold_limit": self.hold_limit}

    def _st(self, sym: str) -> _DayState:
        return self.state.setdefault(sym, _DayState())

    def on_bar(self, bar: Bar) -> Signal:
        st = self._st(bar.symbol)
        t = _et(bar.ts)
        day = t.date()
        if st.day != day:
            was_open = st.side is not Side.FLAT
            st.day = day
            st.vwap_num = st.vwap_den = 0.0
            st.bars_seen = 0
            if was_open:
                st.side = Side.FLAT
                return Signal(Action.EXIT, "new session, flattening", 0.0)

        typical = (bar.high + bar.low + bar.close) / 3.0
        vol = bar.volume if bar.volume > 0 else 1.0
        st.vwap_num += typical * vol
        st.vwap_den += vol
        st.bars_seen += 1
        vwap = st.vwap_num / st.vwap_den if st.vwap_den else bar.close

        clock = t.time()
        if clock < self.start or clock > self.end:
            if st.side is not Side.FLAT:
                return Signal(Action.EXIT, "session end", 0.0)
            return Signal(Action.HOLD, "outside session", 0.0)
        if st.bars_seen < self.warmup:
            return Signal(Action.HOLD, "vwap warming up", 0.0)

        dev_bps = (bar.close - vwap) / vwap * 10_000.0

        if st.side is not Side.FLAT:
            self._held[bar.symbol] = self._held.get(bar.symbol, 0) + 1
            if st.side is Side.LONG:
                if dev_bps >= 0:
                    return Signal(Action.EXIT, "returned to vwap", dev_bps)
                if dev_bps <= -self.stop_bps:
                    return Signal(Action.EXIT, "stop: stretched further", dev_bps)
            else:
                if dev_bps <= 0:
                    return Signal(Action.EXIT, "returned to vwap", dev_bps)
                if dev_bps >= self.stop_bps:
                    return Signal(Action.EXIT, "stop: stretched further", dev_bps)
            if self._held[bar.symbol] >= self.hold_limit:
                return Signal(Action.EXIT, "hold limit", dev_bps)
            return Signal(Action.HOLD, "holding", dev_bps)

        if dev_bps <= -self.entry_bps:
            return Signal(Action.ENTER_LONG, f"{dev_bps:.0f} bps below vwap", dev_bps)
        if dev_bps >= self.entry_bps:
            return Signal(Action.ENTER_SHORT, f"{dev_bps:.0f} bps above vwap", dev_bps)
        return Signal(Action.HOLD, "near vwap", dev_bps)

    def stop_bps_for(self, bar: Bar) -> float:
        return self.stop_bps

    def mark_entry(self, symbol: str, side: Side, price: float, z: float) -> None:
        st = self._st(symbol)
        st.side = side
        st.entry_price = price
        self._held[symbol] = 0

    def mark_exit(self, symbol: str) -> None:
        self._st(symbol).side = Side.FLAT
        self._held[symbol] = 0


class MomentumContinuation:
    """Ride a sustained intraday move rather than fading it.

    Enters when price has advanced more than `entry_bps` over `lookback` bars,
    in the direction of the move, with a trailing stop. Included as the direct
    counterpart to the reversion idea: if reversion loses, its mirror may be
    what is actually happening in the data.
    """

    name = "momentum"

    def __init__(self, lookback: int = 30, entry_bps: float = 30.0,
                 trail_bps: float = 25.0, hold_limit: int = 120,
                 session_start: str = "09:45", session_end: str = "15:50"):
        self.lookback = lookback
        self.entry_bps = entry_bps
        self.trail_bps = trail_bps
        self.hold_limit = hold_limit
        self.start = _hhmm(session_start)
        self.end = _hhmm(session_end)
        self.state: dict[str, _DayState] = {}
        self._held: dict[str, int] = {}
        self._peak: dict[str, float] = {}

    def params(self) -> dict:
        return {"lookback": self.lookback, "entry_bps": self.entry_bps,
                "trail_bps": self.trail_bps, "hold_limit": self.hold_limit}

    def _st(self, sym: str) -> _DayState:
        return self.state.setdefault(sym, _DayState())

    def on_bar(self, bar: Bar) -> Signal:
        st = self._st(bar.symbol)
        t = _et(bar.ts)
        day = t.date()
        if st.day != day:
            was_open = st.side is not Side.FLAT
            st.day = day
            st.closes.clear()
            if was_open:
                st.side = Side.FLAT
                return Signal(Action.EXIT, "new session, flattening", 0.0)
        st.closes.append(bar.close)

        clock = t.time()
        if clock < self.start or clock > self.end:
            if st.side is not Side.FLAT:
                return Signal(Action.EXIT, "session end", 0.0)
            return Signal(Action.HOLD, "outside session", 0.0)
        if len(st.closes) <= self.lookback:
            return Signal(Action.HOLD, "warming up", 0.0)

        past = st.closes[-self.lookback - 1]
        move_bps = (bar.close - past) / past * 10_000.0

        if st.side is not Side.FLAT:
            self._held[bar.symbol] = self._held.get(bar.symbol, 0) + 1
            peak = self._peak.get(bar.symbol, bar.close)
            if st.side is Side.LONG:
                peak = max(peak, bar.high)
                self._peak[bar.symbol] = peak
                if (peak - bar.close) / peak * 10_000.0 >= self.trail_bps:
                    return Signal(Action.EXIT, "trailing stop", move_bps)
            else:
                peak = min(peak, bar.low)
                self._peak[bar.symbol] = peak
                if (bar.close - peak) / peak * 10_000.0 >= self.trail_bps:
                    return Signal(Action.EXIT, "trailing stop", move_bps)
            if self._held[bar.symbol] >= self.hold_limit:
                return Signal(Action.EXIT, "hold limit", move_bps)
            return Signal(Action.HOLD, "riding", move_bps)

        if move_bps >= self.entry_bps:
            return Signal(Action.ENTER_LONG, f"up {move_bps:.0f} bps", move_bps)
        if move_bps <= -self.entry_bps:
            return Signal(Action.ENTER_SHORT, f"down {move_bps:.0f} bps", move_bps)
        return Signal(Action.HOLD, "no thrust", move_bps)

    def stop_bps_for(self, bar: Bar) -> float:
        return self.trail_bps

    def mark_entry(self, symbol: str, side: Side, price: float, z: float) -> None:
        st = self._st(symbol)
        st.side = side
        st.entry_price = price
        self._held[symbol] = 0
        self._peak[symbol] = price

    def mark_exit(self, symbol: str) -> None:
        self._st(symbol).side = Side.FLAT
        self._held[symbol] = 0
        self._peak.pop(symbol, None)
