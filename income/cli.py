"""Command line entry point for the income platform.

    python -m income.cli size            # how much capital is needed
    python -m income.cli plan            # 12-month forecast at current settings
    python -m income.cli status          # account + ledger snapshot
    python -m income.cli deploy          # one-time: invest cash, hold reserve
    python -m income.cli run --period YYYY-MM   # monthly cycle + report
    python -m income.cli simulate --months 24   # end-to-end dry run
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from income.accounts import Account, AlpacaAccount, SimulatedAccount
from income.config import IncomeConfig
from income.engine import IncomeEngine
from income.ledger import Ledger
from income.report import build_report, write_report
from income.sizing import monthly_income, size_portfolio


SIM_STATE = "data/sim_account.json"


def _account(cfg: IncomeConfig, args) -> Account:
    """Simulated unless USE_ALPACA=true. Simulated state persists to disk so
    deploy/run/status chain together exactly as they would against a broker."""
    if getattr(args, "simulated", False) or os.getenv("USE_ALPACA", "").lower() != "true":
        seed = size_portfolio(cfg.target_monthly, cfg.planning_yield, reserve_months=cfg.min_reserve_months)
        return SimulatedAccount.load(
            SIM_STATE, cash=seed.total_to_deposit, annual_yield=cfg.planning_yield
        )
    return AlpacaAccount()


def _persist(acct: Account) -> None:
    if isinstance(acct, SimulatedAccount):
        acct.save(SIM_STATE)


def cmd_size(cfg: IncomeConfig, args) -> int:
    result = size_portfolio(cfg.target_monthly, args.yield_ or cfg.planning_yield, reserve_months=args.reserve_months if args.reserve_months is not None else cfg.min_reserve_months)
    print(json.dumps(result.as_dict(), indent=2))
    return 0


def cmd_plan(cfg: IncomeConfig, args) -> int:
    s = size_portfolio(cfg.target_monthly, cfg.planning_yield, reserve_months=cfg.min_reserve_months)
    print(f"Target: ${cfg.target_monthly:,.0f}/mo (±${cfg.tolerance:,.0f})  instrument: {cfg.instrument}")
    print(f"Planning yield: {cfg.planning_yield:.2%} net")
    print(f"Principal: ${s.principal_required:,.0f}   Reserve: ${s.starter_reserve:,.0f}   Deposit: ${s.total_to_deposit:,.0f}")
    print()
    print("Sensitivity — monthly income on that principal if yields move:")
    for y in (0.055, 0.05, 0.045, 0.04, 0.035, 0.03, 0.025, 0.02):
        inc = monthly_income(s.principal_required, y)
        flag = "ok" if abs(inc - cfg.target_monthly) <= cfg.tolerance else "reserve covers gap"
        print(f"  {y:>5.2%} -> ${inc:7,.2f}/mo   {flag}")
    return 0


def cmd_status(cfg: IncomeConfig, args) -> int:
    ledger = Ledger.load(cfg.ledger_path)
    acct = _account(cfg, args)
    snap = acct.snapshot(cfg.instrument)
    print(json.dumps({
        "mode": acct.mode,
        "instrument": cfg.instrument,
        "cash": round(snap.cash, 2),
        "shares": round(snap.shares, 4),
        "principal_value": round(snap.principal_value, 2),
        "total_value": round(snap.total, 2),
        "reserve": ledger.reserve,
        "withdrawable_now": round(max(snap.cash - ledger.reserve, 0.0), 2),
        "cycles_run": len(ledger.cycles),
        "lifetime_paid": round(ledger.total_paid(), 2),
    }, indent=2))
    return 0


def cmd_deploy(cfg: IncomeConfig, args) -> int:
    ledger = Ledger.load(cfg.ledger_path)
    acct = _account(cfg, args)
    engine = IncomeEngine(cfg, acct, ledger)
    snap = engine.deploy()
    _persist(acct)
    print(f"Deployed. principal=${snap.principal_value:,.2f} cash=${snap.cash:,.2f} reserve=${ledger.reserve:,.2f}")
    return 0


def cmd_run(cfg: IncomeConfig, args) -> int:
    period = args.period or datetime.now(timezone.utc).strftime("%Y-%m")
    ledger = Ledger.load(cfg.ledger_path)
    if ledger.has_period(period):
        print(f"period {period} already processed; nothing to do")
        return 0
    acct = _account(cfg, args)
    if isinstance(acct, SimulatedAccount):
        acct.accrue(cfg.instrument)  # simulate the distribution landing as cash
    engine = IncomeEngine(cfg, acct, ledger)
    result = engine.run_cycle(period)
    _persist(acct)
    path = write_report(cfg, ledger, result)
    print(build_report(cfg, ledger, result))
    print(f"report written: {path}")
    return 0


def cmd_simulate(cfg: IncomeConfig, args) -> int:
    """Dry run N months in memory, including a yield shock, without touching state."""
    acct = SimulatedAccount(annual_yield=cfg.planning_yield)
    seed = size_portfolio(cfg.target_monthly, cfg.planning_yield, reserve_months=cfg.min_reserve_months)
    acct.cash = seed.total_to_deposit
    ledger = Ledger.load("/dev/null") if False else Ledger(path=__import__("pathlib").Path(args.out))
    engine = IncomeEngine(cfg, acct, ledger)
    engine.deploy()

    print(f"{'month':>7} {'yield':>7} {'income':>9} {'payout':>9} {'reserve':>10} {'principal':>12}")
    for i in range(args.months):
        if args.shock_month and i == args.shock_month:
            acct.annual_yield = args.shock_yield
        acct.accrue(cfg.instrument)  # distribution lands in cash
        period = f"sim-{i:03d}"
        res = engine.run_cycle(period)
        r = res.record
        acct.cash -= r.payout  # owner withdraws
        print(f"{i:>7} {acct.annual_yield:>6.2%} {r.income_received:>9,.2f} {r.payout:>9,.2f} "
              f"{r.reserve_after:>10,.2f} {r.principal_value:>12,.2f}")
    print(f"\nTotal paid over {args.months} months: ${ledger.total_paid():,.2f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    cfg = IncomeConfig.from_env()
    p = argparse.ArgumentParser(prog="income", description="Fixed monthly cash income platform")
    p.add_argument("--simulated", action="store_true", help="force the in-memory account")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("size", help="capital required for the target")
    s.add_argument("--yield", dest="yield_", type=float, default=None)
    s.add_argument("--reserve-months", type=float, default=None)
    s.set_defaults(func=cmd_size)

    s = sub.add_parser("plan", help="forecast and yield sensitivity")
    s.set_defaults(func=cmd_plan)

    s = sub.add_parser("status", help="account and ledger snapshot")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("deploy", help="one-time capital deployment")
    s.set_defaults(func=cmd_deploy)

    s = sub.add_parser("run", help="run the monthly cycle")
    s.add_argument("--period", default=None, help="YYYY-MM (default: current month)")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("simulate", help="multi-month dry run")
    s.add_argument("--months", type=int, default=24)
    s.add_argument("--shock-month", type=int, default=None)
    s.add_argument("--shock-yield", type=float, default=0.02)
    s.add_argument("--out", default="/tmp/sim_ledger.json")
    s.set_defaults(func=cmd_simulate)

    args = p.parse_args(argv)
    return args.func(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
