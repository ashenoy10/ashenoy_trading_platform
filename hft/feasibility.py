"""Can $3,000 produce $500 a month by scalping? This module answers it.

The arithmetic is not a matter of opinion. Monthly profit is

    profit = trades * notional * (edge_bps - cost_bps) / 10_000

so for a fixed capital base and a fixed cost structure, the required edge per
trade is pinned by the trade count. This module inverts the equation and
reports what the strategy would have to achieve, so the number can be
compared against what is actually observable in the market.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from hft.config import CostModel
from hft.costs import round_trip_cost_bps


@dataclass(frozen=True)
class EdgeRequirement:
    trades_per_month: int
    notional_per_trade: float
    cost_bps: float
    required_net_bps: float
    required_gross_bps: float
    required_gross_dollars: float
    cost_dollars_per_month: float

    def as_dict(self) -> dict:
        return {
            "trades_per_month": self.trades_per_month,
            "notional_per_trade": round(self.notional_per_trade, 2),
            "cost_bps_round_trip": round(self.cost_bps, 3),
            "required_net_bps_per_trade": round(self.required_net_bps, 3),
            "required_gross_bps_per_trade": round(self.required_gross_bps, 3),
            "required_gross_dollars_per_trade": round(self.required_gross_dollars, 4),
            "cost_dollars_per_month": round(self.cost_dollars_per_month, 2),
        }


def required_edge(
    target: float,
    trades_per_month: int,
    notional_per_trade: float,
    model: CostModel,
    price: float = 650.0,
) -> EdgeRequirement:
    """Gross per-trade edge needed to clear `target` after all costs."""
    if trades_per_month <= 0:
        raise ValueError("trades_per_month must be positive")
    cost_bps = round_trip_cost_bps(model, notional_per_trade, price)
    net_dollars_needed = target / trades_per_month
    net_bps = net_dollars_needed / notional_per_trade * 10_000.0
    gross_bps = net_bps + cost_bps
    return EdgeRequirement(
        trades_per_month=trades_per_month,
        notional_per_trade=notional_per_trade,
        cost_bps=cost_bps,
        required_net_bps=net_bps,
        required_gross_bps=gross_bps,
        required_gross_dollars=gross_bps / 10_000.0 * notional_per_trade,
        cost_dollars_per_month=cost_bps / 10_000.0 * notional_per_trade * trades_per_month,
    )


def required_win_rate(gross_bps: float, win_bps: float, loss_bps: float) -> float:
    """Win rate needed for an average gross edge, given fixed win/loss sizes.

    p*win - (1-p)*loss = gross  =>  p = (gross + loss) / (win + loss)
    """
    if win_bps + loss_bps <= 0:
        raise ValueError("win_bps + loss_bps must be positive")
    return (gross_bps + loss_bps) / (win_bps + loss_bps)


def monthly_return_required(target: float, capital: float) -> float:
    return target / capital


def annualized_from_monthly(monthly: float) -> float:
    return (1.0 + monthly) ** 12 - 1.0


def risk_of_ruin(
    win_rate: float,
    win_amount: float,
    loss_amount: float,
    capital: float,
    ruin_level: float,
    trades: int,
    simulations: int = 20_000,
    seed: int = 7,
) -> float:
    """Monte Carlo probability of equity touching `ruin_level` within `trades`.

    Independent, identically distributed trades. Real losing streaks cluster,
    so treat this as an optimistic lower bound on the true risk.
    """
    rng = random.Random(seed)
    ruined = 0
    for _ in range(simulations):
        equity = capital
        for _ in range(trades):
            equity += win_amount if rng.random() < win_rate else -loss_amount
            if equity <= ruin_level:
                ruined += 1
                break
    return ruined / simulations


def kelly_fraction(win_rate: float, win_amount: float, loss_amount: float) -> float:
    """Optimal growth bet size. Negative means the edge is negative: do not bet."""
    if loss_amount <= 0:
        raise ValueError("loss_amount must be positive")
    b = win_amount / loss_amount
    return (win_rate * (b + 1.0) - 1.0) / b


def break_even_trades(target: float, net_bps: float, notional: float) -> float:
    """Trades needed per month at a given realized net edge."""
    per_trade = net_bps / 10_000.0 * notional
    if per_trade <= 0:
        return math.inf
    return target / per_trade
