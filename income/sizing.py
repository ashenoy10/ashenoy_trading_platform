"""Capital sizing for a constant monthly cash target.

The only way to make a fixed monthly cash number reliable is to hold enough
principal in an instrument whose income is predictable. This module answers
"how much principal do I need" for a given net yield, and how much reserve
protects the payout if yields fall.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SizingResult:
    target_monthly: float
    net_yield: float
    principal_required: float
    starter_reserve: float
    total_to_deposit: float
    expected_monthly_income: float

    def as_dict(self) -> dict:
        return {
            "target_monthly": round(self.target_monthly, 2),
            "net_yield": self.net_yield,
            "principal_required": round(self.principal_required, 2),
            "starter_reserve": round(self.starter_reserve, 2),
            "total_to_deposit": round(self.total_to_deposit, 2),
            "expected_monthly_income": round(self.expected_monthly_income, 2),
        }


def principal_for_target(target_monthly: float, net_yield: float) -> float:
    if net_yield <= 0:
        raise ValueError("net_yield must be positive")
    return target_monthly * 12.0 / net_yield


def monthly_income(principal: float, net_yield: float) -> float:
    return principal * net_yield / 12.0


def size_portfolio(
    target_monthly: float,
    net_yield: float,
    reserve_months: float = 6.0,
    principal_cushion: float = 0.05,
) -> SizingResult:
    """Size principal plus a cash reserve.

    principal_cushion: extra principal beyond the break-even amount so the
        portfolio over-earns slightly in the base case and the surplus keeps
        refilling the reserve.
    reserve_months: months of *target* held as a cash reserve on day one.
        The reserve absorbs months where income lands below target (yield
        dip, distribution timing) so the payout stays exactly on target.
    """
    base = principal_for_target(target_monthly, net_yield)
    principal = base * (1.0 + principal_cushion)
    reserve = target_monthly * reserve_months
    return SizingResult(
        target_monthly=target_monthly,
        net_yield=net_yield,
        principal_required=principal,
        starter_reserve=reserve,
        total_to_deposit=principal + reserve,
        expected_monthly_income=monthly_income(principal, net_yield),
    )


def months_of_cover(reserve: float, principal: float, net_yield: float, target_monthly: float) -> float | None:
    """How many months the reserve lasts if income stays at net_yield.

    Returns None when income covers the target on its own.
    """
    shortfall = target_monthly - monthly_income(principal, net_yield)
    if shortfall <= 0:
        return None
    return reserve / shortfall


if __name__ == "__main__":
    import json
    import sys

    target = float(sys.argv[1]) if len(sys.argv) > 1 else 500.0
    y = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0395
    print(json.dumps(size_portfolio(target, y).as_dict(), indent=2))
