import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

from income.accounts import SimulatedAccount
from income.config import IncomeConfig
from income.engine import IncomeEngine
from income.ledger import Ledger
from income.report import build_report
from income.sizing import monthly_income, months_of_cover, principal_for_target, size_portfolio


def make(tmp_path: Path, yld: float = 0.0395, target: float = 500.0):
    cfg = IncomeConfig(target_monthly=target, planning_yield=yld, ledger_path=str(tmp_path / "l.json"))
    acct = SimulatedAccount(annual_yield=yld)
    acct.cash = size_portfolio(target, yld, reserve_months=cfg.min_reserve_months).total_to_deposit
    ledger = Ledger(path=Path(cfg.ledger_path))
    engine = IncomeEngine(cfg, acct, ledger)
    engine.deploy()
    return cfg, acct, ledger, engine


# -- sizing ---------------------------------------------------------------

def test_principal_for_target_is_exact():
    p = principal_for_target(500.0, 0.04)
    assert math.isclose(monthly_income(p, 0.04), 500.0)


def test_principal_scales_inversely_with_yield():
    assert principal_for_target(500, 0.02) == pytest.approx(2 * principal_for_target(500, 0.04))


def test_zero_or_negative_yield_rejected():
    with pytest.raises(ValueError):
        principal_for_target(500.0, 0.0)


def test_size_portfolio_includes_cushion_and_reserve():
    s = size_portfolio(500.0, 0.04, reserve_months=6.0, principal_cushion=0.05)
    assert s.starter_reserve == 3000.0
    assert s.expected_monthly_income > 500.0
    assert s.total_to_deposit == pytest.approx(s.principal_required + s.starter_reserve)


def test_months_of_cover_none_when_income_sufficient():
    assert months_of_cover(3000, 200_000, 0.04, 500) is None


def test_months_of_cover_counts_shortfall():
    # 100k at 2% = $166.67/mo, shortfall $333.33, reserve 3000 -> 9 months
    assert months_of_cover(3000, 100_000, 0.02, 500) == pytest.approx(9.0, abs=0.05)


# -- deployment -----------------------------------------------------------

def test_deploy_keeps_reserve_in_cash(tmp_path):
    cfg, acct, ledger, engine = make(tmp_path)
    assert ledger.reserve == pytest.approx(3000.0)
    assert acct.cash == pytest.approx(3000.0)
    assert acct.shares > 0


# -- monthly cycle --------------------------------------------------------

def test_cycle_pays_exactly_target(tmp_path):
    cfg, acct, ledger, engine = make(tmp_path)
    acct.accrue(cfg.instrument)
    res = engine.run_cycle("2026-10")
    assert res.record.payout == pytest.approx(500.0)


def test_surplus_is_reinvested_not_paid_out(tmp_path):
    cfg, acct, ledger, engine = make(tmp_path)
    before = acct.shares
    acct.accrue(cfg.instrument)
    res = engine.run_cycle("2026-10")
    assert res.record.payout == 500.0
    assert res.record.reinvested > 0
    assert acct.shares > before


def test_reserve_absorbs_yield_collapse(tmp_path):
    cfg, acct, ledger, engine = make(tmp_path)
    acct.annual_yield = 0.01
    acct.accrue(cfg.instrument)
    res = engine.run_cycle("2026-10")
    assert res.record.payout == pytest.approx(500.0)
    assert res.record.reserve_after < res.record.reserve_before
    assert res.record.principal_sold == 0.0


def test_principal_sold_only_after_reserve_exhausted(tmp_path):
    cfg, acct, ledger, engine = make(tmp_path)
    acct.annual_yield = 0.0
    ledger.reserve = 100.0
    res = engine.run_cycle("2026-10")
    assert res.record.payout == pytest.approx(500.0)
    assert res.record.principal_sold == pytest.approx(400.0)
    assert res.record.reserve_after == pytest.approx(0.0)


def test_payout_never_exceeds_target_even_with_huge_income(tmp_path):
    cfg, acct, ledger, engine = make(tmp_path)
    acct.cash += 50_000.0  # windfall
    res = engine.run_cycle("2026-10")
    assert res.record.payout == pytest.approx(500.0)


def test_payout_reduced_and_flagged_when_principal_exhausted(tmp_path):
    cfg = IncomeConfig(ledger_path=str(tmp_path / "l.json"))
    acct = SimulatedAccount(annual_yield=0.0, cash=0.0, shares=1.0, price=50.0)
    engine = IncomeEngine(cfg, acct, Ledger(path=Path(cfg.ledger_path)))
    res = engine.run_cycle("2026-10")
    assert res.record.payout == pytest.approx(50.0)
    assert any("PRINCIPAL EXHAUSTED" in n for n in res.record.notes)


def test_duplicate_period_rejected(tmp_path):
    cfg, acct, ledger, engine = make(tmp_path)
    acct.accrue(cfg.instrument)
    engine.run_cycle("2026-10")
    with pytest.raises(RuntimeError):
        engine.run_cycle("2026-10")


def test_low_reserve_raises_action_note(tmp_path):
    cfg, acct, ledger, engine = make(tmp_path)
    acct.annual_yield = 0.0
    ledger.reserve = 900.0
    res = engine.run_cycle("2026-10")
    assert any(n.startswith("ACTION") for n in res.record.notes)


# -- persistence + reporting ---------------------------------------------

def test_ledger_round_trips(tmp_path):
    cfg, acct, ledger, engine = make(tmp_path)
    acct.accrue(cfg.instrument)
    engine.run_cycle("2026-10")
    reloaded = Ledger.load(cfg.ledger_path)
    assert reloaded.reserve == pytest.approx(ledger.reserve)
    assert reloaded.cycles[0].payout == 500.0
    assert reloaded.total_paid() == 500.0


def test_report_states_payout_and_status(tmp_path):
    cfg, acct, ledger, engine = make(tmp_path)
    acct.accrue(cfg.instrument)
    res = engine.run_cycle("2026-10")
    md = build_report(cfg, ledger, res)
    assert "ON TARGET" in md
    assert "$500.00" in md


def test_twelve_months_pay_exactly_target_each_time(tmp_path):
    cfg, acct, ledger, engine = make(tmp_path)
    for i in range(12):
        acct.accrue(cfg.instrument)
        res = engine.run_cycle(f"2026-{i:02d}")
        acct.cash -= res.record.payout
        assert res.record.payout == pytest.approx(500.0)
    assert ledger.total_paid() == pytest.approx(6000.0)
    assert acct.shares * acct.price > 159_000  # principal preserved


# -- safety ---------------------------------------------------------------

def test_alpaca_account_refuses_orders_without_explicit_flag(monkeypatch):
    from income.accounts import AlpacaAccount
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setenv("INCOME_LIVE_ORDERS", "false")
    a = AlpacaAccount(base_url="https://paper-api.alpaca.markets")
    with pytest.raises(PermissionError):
        a.buy_notional("SGOV", 100.0)
    with pytest.raises(PermissionError):
        a.sell_notional("SGOV", 100.0)


def test_simulated_account_state_round_trips(tmp_path):
    a = SimulatedAccount(cash=123.45, shares=6.5, price=100.0, annual_yield=0.03)
    a.save(tmp_path / "s.json")
    b = SimulatedAccount.load(tmp_path / "s.json")
    assert (b.cash, b.shares, b.price, b.annual_yield) == (123.45, 6.5, 100.0, 0.03)


def test_simulated_account_load_uses_defaults_when_absent(tmp_path):
    b = SimulatedAccount.load(tmp_path / "missing.json", cash=999.0)
    assert b.cash == 999.0


def test_unwithdrawn_cash_accumulates_when_owner_does_not_withdraw(tmp_path):
    cfg, acct, ledger, engine = make(tmp_path)
    acct.accrue(cfg.instrument)
    first = engine.run_cycle("2026-09")
    acct.accrue(cfg.instrument)
    second = engine.run_cycle("2026-10")
    assert first.unwithdrawn_cash == pytest.approx(500.0)
    assert second.unwithdrawn_cash == pytest.approx(1000.0)
