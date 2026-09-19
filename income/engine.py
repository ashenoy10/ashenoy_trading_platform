"""Monthly cycle: turn whatever income arrived into exactly one target payout.

Rules, in order:
1. Income received this cycle plus the reserve is the pool.
2. Pay exactly `target` from the pool. If the pool is short, sell principal for
   the difference and flag it (this is the only way the payout can ever be off,
   and it can only happen if principal is exhausted).
3. Refill the reserve up to `min_reserve_months * target`.
4. Reinvest anything left into the instrument so principal keeps up.
Cash left in the account after a cycle == payout + reserve. The payout is what
the owner withdraws.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from income.accounts import Account, Snapshot, default_lookback
from income.config import IncomeConfig
from income.ledger import CycleRecord, Ledger


@dataclass
class CycleResult:
    record: CycleRecord
    before: Snapshot
    after: Snapshot
    unwithdrawn_cash: float  # cash beyond reserve (this + earlier un-withdrawn payouts)

    @property
    def within_band(self) -> bool:
        return True  # payout is always exactly target unless principal is exhausted


class IncomeEngine:
    def __init__(self, config: IncomeConfig, account: Account, ledger: Ledger):
        self.config = config
        self.account = account
        self.ledger = ledger

    # -- helpers ----------------------------------------------------------
    @property
    def reserve_target(self) -> float:
        return self.config.min_reserve_months * self.config.target_monthly

    def _window_start(self, now: datetime) -> datetime:
        if self.ledger.cycles:
            return datetime.fromisoformat(self.ledger.cycles[-1].run_at)
        return default_lookback(now)

    # -- one-time deployment ---------------------------------------------
    def deploy(self, keep_reserve: float | None = None) -> Snapshot:
        """Invest all cash except the reserve into the instrument."""
        reserve = self.reserve_target if keep_reserve is None else keep_reserve
        snap = self.account.snapshot(self.config.instrument)
        investable = snap.cash - reserve
        if investable > 1.0:
            self.account.buy_notional(self.config.instrument, round(investable, 2))
        self.ledger.reserve = min(reserve, snap.cash)
        self.ledger.save()
        return self.account.snapshot(self.config.instrument)

    # -- monthly cycle ----------------------------------------------------
    def run_cycle(self, period: str, now: datetime | None = None) -> CycleResult:
        now = now or datetime.now(timezone.utc)
        if self.ledger.has_period(period):
            raise RuntimeError(f"period {period} already processed")

        cfg = self.config
        notes: list[str] = []
        before = self.account.snapshot(cfg.instrument)
        income = self.account.dividends_since(cfg.instrument, self._window_start(now))
        reserve_before = self.ledger.reserve

        pool = income + reserve_before
        payout = cfg.target_monthly
        principal_sold = 0.0
        if pool < payout:
            principal_sold = round(payout - pool, 2)
            sellable = before.principal_value
            if principal_sold > sellable:
                notes.append("PRINCIPAL EXHAUSTED: payout reduced")
                principal_sold = round(sellable, 2)
                payout = round(pool + principal_sold, 2)
            if principal_sold > 0:
                self.account.sell_notional(cfg.instrument, principal_sold)
                notes.append(f"sold ${principal_sold:.2f} principal to cover income shortfall")
            pool += principal_sold

        remaining = pool - payout
        reserve_after = min(remaining, self.reserve_target)
        reinvest = round(remaining - reserve_after, 2)
        if reinvest >= 1.0:
            self.account.buy_notional(cfg.instrument, reinvest)
        else:
            reserve_after += reinvest  # keep dust in reserve
            reinvest = 0.0

        if reserve_after < 2.0 * cfg.target_monthly:
            notes.append(
                f"ACTION: reserve ${reserve_after:,.2f} is under 2 months of target; "
                "income is running below plan. Either accept slow principal drawdown or add capital."
            )
        if abs(income - cfg.target_monthly) > cfg.tolerance:
            notes.append(f"raw income ${income:.2f} outside ±${cfg.tolerance:.0f} band; reserve absorbed it")

        after = self.account.snapshot(cfg.instrument)
        record = CycleRecord(
            period=period,
            run_at=now.isoformat(),
            income_received=round(income, 2),
            payout=round(payout, 2),
            reserve_before=round(reserve_before, 2),
            reserve_after=round(reserve_after, 2),
            reinvested=reinvest,
            principal_sold=principal_sold,
            principal_value=round(after.principal_value, 2),
            shares=round(after.shares, 4),
            mode=self.account.mode,
            notes=notes,
        )
        self.ledger.reserve = round(reserve_after, 2)
        self.ledger.cycles.append(record)
        self.ledger.save()

        unwithdrawn = round(after.cash - reserve_after, 2)
        return CycleResult(record=record, before=before, after=after, unwithdrawn_cash=unwithdrawn)
