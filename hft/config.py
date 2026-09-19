"""All tunable parameters. Every field is environment-overridable."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _f(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _i(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return raw.lower() == "true" if raw not in (None, "") else default


@dataclass(frozen=True)
class CostModel:
    """Round-trip trading costs. Defaults are for a liquid US equity ETF.

    sec_fee_per_million: SEC Section 31 fee, charged on SELLS only. The rate
        rose to $20.60 per $1M of principal effective 4 April 2026.
    finra_taf_per_share: FINRA Trading Activity Fee, sells only, capped per trade.
    spread_bps: full quoted bid-ask spread in basis points of price.
    spread_capture: fraction of the spread you actually pay per side. 1.0 means
        you cross the full half-spread each way (marketable orders). Passive
        limit orders can pay less, but they also do not always fill.
    slippage_bps: additional adverse fill cost per side beyond the quoted spread.
    commission_per_trade: $0 at Alpaca for US equities.
    """

    sec_fee_per_million: float = 20.60
    finra_taf_per_share: float = 0.000166
    finra_taf_cap: float = 8.30
    spread_bps: float = 1.5
    spread_capture: float = 1.0
    slippage_bps: float = 0.5
    commission_per_trade: float = 0.0

    @classmethod
    def from_env(cls) -> "CostModel":
        return cls(
            sec_fee_per_million=_f("COST_SEC_FEE_PER_M", 20.60),
            finra_taf_per_share=_f("COST_TAF_PER_SHARE", 0.000166),
            finra_taf_cap=_f("COST_TAF_CAP", 8.30),
            spread_bps=_f("COST_SPREAD_BPS", 1.5),
            spread_capture=_f("COST_SPREAD_CAPTURE", 1.0),
            slippage_bps=_f("COST_SLIPPAGE_BPS", 0.5),
            commission_per_trade=_f("COST_COMMISSION", 0.0),
        )


@dataclass(frozen=True)
class RiskLimits:
    """Hard limits. The engine refuses to trade once any of these is breached.

    On $3,000 the dominant risk is not underperformance, it is ruin. These
    limits exist to make ruin structurally difficult rather than unlikely.
    """

    max_daily_loss: float = 90.0          # 3% of capital
    max_monthly_loss: float = 300.0       # 10% of capital — hard stop for the month
    max_trade_risk: float = 15.0          # 0.5% of capital per trade
    max_position_notional: float = 3000.0 # no leverage by default
    max_consecutive_losses: int = 6
    max_trades_per_day: int = 40
    min_equity: float = 2400.0            # 80% of starting capital: full halt

    @classmethod
    def from_env(cls, capital: float) -> "RiskLimits":
        return cls(
            max_daily_loss=_f("RISK_MAX_DAILY_LOSS", 0.03 * capital),
            max_monthly_loss=_f("RISK_MAX_MONTHLY_LOSS", 0.10 * capital),
            max_trade_risk=_f("RISK_MAX_TRADE_RISK", 0.005 * capital),
            max_position_notional=_f("RISK_MAX_POSITION", capital),
            max_consecutive_losses=_i("RISK_MAX_CONSEC_LOSSES", 6),
            max_trades_per_day=_i("RISK_MAX_TRADES_PER_DAY", 40),
            min_equity=_f("RISK_MIN_EQUITY", 0.80 * capital),
        )


@dataclass(frozen=True)
class StrategyParams:
    """Short-horizon mean reversion on 1-minute bars.

    Enter when price deviates from a short EMA by more than entry_z standard
    deviations, exit on reversion to exit_z, on a stop, or after hold_limit
    bars. Intentionally simple: every parameter is one you can reason about
    and re-fit, and there are few enough of them to avoid curve-fitting $3,000
    into a backtest artifact.
    """

    symbols: tuple[str, ...] = ("SPY", "QQQ")
    lookback: int = 20
    entry_z: float = 2.0
    exit_z: float = 0.3
    stop_z: float = 3.5
    hold_limit: int = 10          # bars
    cooldown_bars: int = 2
    trade_start: str = "09:45"    # skip the opening auction chaos
    trade_end: str = "15:50"      # flatten before the close

    @classmethod
    def from_env(cls) -> "StrategyParams":
        syms = os.getenv("HFT_SYMBOLS", "SPY,QQQ")
        return cls(
            symbols=tuple(s.strip().upper() for s in syms.split(",") if s.strip()),
            lookback=_i("HFT_LOOKBACK", 20),
            entry_z=_f("HFT_ENTRY_Z", 2.0),
            exit_z=_f("HFT_EXIT_Z", 0.3),
            stop_z=_f("HFT_STOP_Z", 3.5),
            hold_limit=_i("HFT_HOLD_LIMIT", 10),
            cooldown_bars=_i("HFT_COOLDOWN", 2),
            trade_start=os.getenv("HFT_TRADE_START", "09:45"),
            trade_end=os.getenv("HFT_TRADE_END", "15:50"),
        )


@dataclass(frozen=True)
class Config:
    capital: float = 3000.0
    monthly_target: float = 500.0
    target_tolerance: float = 50.0
    stop_at_target: bool = True
    state_path: str = "data/hft_state.json"
    costs: CostModel = field(default_factory=CostModel)
    strategy: StrategyParams = field(default_factory=StrategyParams)
    risk: RiskLimits = field(default_factory=RiskLimits)

    @classmethod
    def from_env(cls) -> "Config":
        capital = _f("HFT_CAPITAL", 3000.0)
        return cls(
            capital=capital,
            monthly_target=_f("HFT_MONTHLY_TARGET", 500.0),
            target_tolerance=_f("HFT_TARGET_TOLERANCE", 50.0),
            stop_at_target=_b("HFT_STOP_AT_TARGET", True),
            state_path=os.getenv("HFT_STATE_PATH", "data/hft_state.json"),
            costs=CostModel.from_env(),
            strategy=StrategyParams.from_env(),
            risk=RiskLimits.from_env(capital),
        )
