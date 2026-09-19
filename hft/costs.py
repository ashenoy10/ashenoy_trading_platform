"""Realistic round-trip cost accounting.

Scalping lives or dies here. A strategy that looks profitable on mid prices
is usually a losing strategy once the spread, slippage and regulatory fees
are charged, because the per-trade edge being hunted is the same order of
magnitude as the per-trade cost.
"""

from __future__ import annotations

from dataclasses import dataclass

from hft.config import CostModel


@dataclass(frozen=True)
class TradeCost:
    spread: float
    slippage: float
    sec_fee: float
    taf: float
    commission: float

    @property
    def total(self) -> float:
        return self.spread + self.slippage + self.sec_fee + self.taf + self.commission

    def as_dict(self) -> dict:
        return {
            "spread": round(self.spread, 4),
            "slippage": round(self.slippage, 4),
            "sec_fee": round(self.sec_fee, 4),
            "taf": round(self.taf, 4),
            "commission": round(self.commission, 4),
            "total": round(self.total, 4),
        }


def one_side_cost(model: CostModel, notional: float, shares: float, is_sell: bool) -> TradeCost:
    """Cost of a single fill. Regulatory fees apply to sells only."""
    half_spread_bps = model.spread_bps / 2.0 * model.spread_capture
    spread = notional * half_spread_bps / 10_000.0
    slippage = notional * model.slippage_bps / 10_000.0
    sec_fee = 0.0
    taf = 0.0
    if is_sell:
        sec_fee = notional * model.sec_fee_per_million / 1_000_000.0
        taf = min(shares * model.finra_taf_per_share, model.finra_taf_cap)
    return TradeCost(
        spread=spread,
        slippage=slippage,
        sec_fee=sec_fee,
        taf=taf,
        commission=model.commission_per_trade,
    )


def round_trip_cost(model: CostModel, notional: float, price: float) -> TradeCost:
    """Cost of entering and exiting one position of the given notional."""
    shares = notional / price if price > 0 else 0.0
    buy = one_side_cost(model, notional, shares, is_sell=False)
    sell = one_side_cost(model, notional, shares, is_sell=True)
    return TradeCost(
        spread=buy.spread + sell.spread,
        slippage=buy.slippage + sell.slippage,
        sec_fee=buy.sec_fee + sell.sec_fee,
        taf=buy.taf + sell.taf,
        commission=buy.commission + sell.commission,
    )


def round_trip_cost_bps(model: CostModel, notional: float, price: float) -> float:
    if notional <= 0:
        return 0.0
    return round_trip_cost(model, notional, price).total / notional * 10_000.0
