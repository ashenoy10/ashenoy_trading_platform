import os
from dataclasses import dataclass


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class IncomeConfig:
    """Parameters for the constant-payout income engine.

    target_monthly: cash to leave available for withdrawal each month.
    tolerance: acceptable band around the target (report-only; the engine
        always pays exactly target when it can).
    instrument: ticker of the Treasury-bill ETF held as principal.
    planning_yield: net annual yield used for sizing and forecasts.
    expense_ratio: instrument's annual expense ratio (already netted out of
        distributions; kept for reporting).
    min_reserve_months: reserve (in months of target) to build before any
        surplus is reinvested into principal.
    """

    target_monthly: float = 500.0
    tolerance: float = 50.0
    instrument: str = "SGOV"
    planning_yield: float = 0.0365
    expense_ratio: float = 0.0009
    min_reserve_months: float = 6.0
    ledger_path: str = "data/income_ledger.json"

    @classmethod
    def from_env(cls) -> "IncomeConfig":
        return cls(
            target_monthly=_env_float("INCOME_TARGET_MONTHLY", 500.0),
            tolerance=_env_float("INCOME_TOLERANCE", 50.0),
            instrument=os.getenv("INCOME_INSTRUMENT", "SGOV"),
            planning_yield=_env_float("INCOME_PLANNING_YIELD", 0.0365),
            expense_ratio=_env_float("INCOME_EXPENSE_RATIO", 0.0009),
            min_reserve_months=_env_float("INCOME_MIN_RESERVE_MONTHS", 6.0),
            ledger_path=os.getenv("INCOME_LEDGER_PATH", "data/income_ledger.json"),
        )
