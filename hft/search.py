"""Strategy search with an out-of-sample holdout.

The danger in "try many strategies, keep the best" is that searching hard
enough on one dataset always produces a winner, and that winner is usually
fitted to noise. This module guards against that in three ways:

1. The data is split by date. Every configuration is fitted and ranked on the
   earlier in-sample period only. The later out-of-sample period is untouched
   until a single finalist is chosen.
2. Ranking uses a t-statistic on per-trade edge, not total profit, so a config
   that made money on six lucky trades cannot outrank a steadier one.
3. The number of configurations tried is reported, because the best of K
   random configs looks good in proportion to K. The out-of-sample result is
   the only number that means anything, and it is reported unadjusted.

A strategy that looks strong in-sample and falls apart out-of-sample was
curve-fit. That is a finding, not a failure, and it is far cheaper to learn
here than with real money.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, replace
from typing import Callable, Iterable

from hft.backtest import Backtester, BacktestResult
from hft.config import Config
from hft.signals import MomentumContinuation, OpeningRangeBreakout, VWAPReversion
from hft.strategy import Bar


@dataclass
class Candidate:
    name: str
    params: dict
    factory: Callable[[], object]


@dataclass
class Evaluation:
    candidate: Candidate
    result: BacktestResult

    @property
    def trades(self) -> int:
        return len(self.result.trades)

    @property
    def net(self) -> float:
        return self.result.net_pnl

    @property
    def bps(self) -> float:
        xs = self.result.net_bps_series
        return sum(xs) / len(xs) if xs else 0.0

    @property
    def t(self) -> float:
        return self.result.t_stat


# -- the grids ------------------------------------------------------------

def build_grid() -> list[Candidate]:
    out: list[Candidate] = []

    for orm, sm, tm, mr in itertools.product(
        (5, 15, 30), (0.5, 1.0), (1.0, 2.0, 3.0), (10.0, 20.0)
    ):
        out.append(Candidate("orb",
            {"or_minutes": orm, "stop_mult": sm, "target_mult": tm, "min_range_bps": mr},
            lambda orm=orm, sm=sm, tm=tm, mr=mr: OpeningRangeBreakout(
                or_minutes=orm, stop_mult=sm, target_mult=tm, min_range_bps=mr)))

    for eb, sb, hl in itertools.product(
        (25.0, 40.0, 60.0, 80.0), (50.0, 80.0, 120.0), (60, 120, 240)
    ):
        out.append(Candidate("vwap_reversion",
            {"entry_bps": eb, "stop_bps": sb, "hold_limit": hl},
            lambda eb=eb, sb=sb, hl=hl: VWAPReversion(
                entry_bps=eb, stop_bps=sb, hold_limit=hl)))

    for lb, eb, tb, hl in itertools.product(
        (15, 30, 60), (20.0, 40.0, 60.0), (15.0, 30.0, 50.0), (60, 180)
    ):
        out.append(Candidate("momentum",
            {"lookback": lb, "entry_bps": eb, "trail_bps": tb, "hold_limit": hl},
            lambda lb=lb, eb=eb, tb=tb, hl=hl: MomentumContinuation(
                lookback=lb, entry_bps=eb, trail_bps=tb, hold_limit=hl)))

    return out


# -- the split ------------------------------------------------------------

def split_bars(bars: list[Bar], oos_fraction: float = 0.3) -> tuple[list[Bar], list[Bar]]:
    """Chronological split. Never random: shuffling leaks the future."""
    if not bars:
        return [], []
    days = sorted({(b.ts.date() if hasattr(b.ts, "date") else b.ts) for b in bars})
    cut_idx = int(len(days) * (1.0 - oos_fraction))
    cut = days[max(cut_idx, 1) - 1]
    in_s = [b for b in bars if (b.ts.date() if hasattr(b.ts, "date") else b.ts) <= cut]
    out_s = [b for b in bars if (b.ts.date() if hasattr(b.ts, "date") else b.ts) > cut]
    return in_s, out_s


def evaluate(cfg: Config, bars: list[Bar], cand: Candidate) -> Evaluation:
    # The profit governor must be off during research: halting at the target
    # truncates the sample and biases every statistic computed from it.
    research_cfg = replace(cfg, stop_at_target=False, monthly_target=1e12)
    res = Backtester(research_cfg).run(bars, strategy=cand.factory())
    return Evaluation(candidate=cand, result=res)


def search(cfg: Config, bars: list[Bar], oos_fraction: float = 0.3,
           min_trades: int = 30, top_n: int = 5) -> dict:
    in_s, out_s = split_bars(bars, oos_fraction)
    grid = build_grid()

    ranked: list[Evaluation] = []
    for cand in grid:
        ev = evaluate(cfg, in_s, cand)
        if ev.trades >= min_trades:
            ranked.append(ev)
    ranked.sort(key=lambda e: e.t, reverse=True)

    finalists = []
    for ev in ranked[:top_n]:
        oos = evaluate(cfg, out_s, ev.candidate)
        finalists.append({
            "name": ev.candidate.name,
            "params": ev.candidate.params,
            "in_sample": {"trades": ev.trades, "net": round(ev.net, 2),
                          "bps": round(ev.bps, 3), "t": round(ev.t, 2)},
            "out_of_sample": {"trades": oos.trades, "net": round(oos.net, 2),
                              "bps": round(oos.bps, 3), "t": round(oos.t, 2)},
        })

    def day_of(b):
        return b.ts.date() if hasattr(b.ts, "date") else b.ts

    return {
        "configs_tried": len(grid),
        "configs_with_enough_trades": len(ranked),
        "in_sample_days": len({day_of(b) for b in in_s}),
        "out_of_sample_days": len({day_of(b) for b in out_s}),
        "in_sample_bars": len(in_s),
        "out_of_sample_bars": len(out_s),
        "finalists": finalists,
    }
