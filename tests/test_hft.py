from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from hft.backtest import Backtester
from hft.config import Config, CostModel, RiskLimits, StrategyParams
from hft.costs import one_side_cost, round_trip_cost, round_trip_cost_bps
from hft.feasibility import (break_even_trades, kelly_fraction, required_edge,
                             required_win_rate, risk_of_ruin)
from hft.marketdata import SyntheticBars
from hft.risk import ProfitGovernor, RiskManager, position_notional
from hft.runner import Runner
from hft.state import MonthState
from hft.strategy import Action, Bar, ScalpStrategy, Side


# -- cost model -----------------------------------------------------------

def test_regulatory_fees_apply_to_sells_only():
    m = CostModel()
    buy = one_side_cost(m, 3000.0, 4.6, is_sell=False)
    sell = one_side_cost(m, 3000.0, 4.6, is_sell=True)
    assert buy.sec_fee == 0.0 and buy.taf == 0.0
    assert sell.sec_fee > 0.0 and sell.taf > 0.0


def test_sec_fee_matches_published_rate():
    # $20.60 per $1,000,000 => $0.0618 on $3,000
    m = CostModel()
    c = one_side_cost(m, 3000.0, 4.6, is_sell=True)
    assert c.sec_fee == pytest.approx(0.0618, abs=1e-6)


def test_taf_is_capped():
    m = CostModel()
    c = one_side_cost(m, 1e9, 1e9, is_sell=True)
    assert c.taf == m.finra_taf_cap


def test_round_trip_cost_scales_with_notional():
    m = CostModel()
    a = round_trip_cost(m, 1000.0, 650.0).total
    b = round_trip_cost(m, 2000.0, 650.0).total
    assert b > a


def test_zero_spread_and_slippage_leaves_only_regulatory_fees():
    m = CostModel(spread_bps=0.0, slippage_bps=0.0)
    c = round_trip_cost(m, 3000.0, 650.0)
    assert c.spread == 0.0 and c.slippage == 0.0
    assert c.total == pytest.approx(c.sec_fee + c.taf)


# -- feasibility ----------------------------------------------------------

def test_required_edge_falls_as_trade_count_rises():
    m = CostModel()
    few = required_edge(500.0, 50, 3000.0, m)
    many = required_edge(500.0, 500, 3000.0, m)
    assert many.required_gross_bps < few.required_gross_bps


def test_total_costs_rise_as_trade_count_rises():
    """The core tension: volume lowers the per-trade bar but raises total cost."""
    m = CostModel()
    few = required_edge(500.0, 50, 3000.0, m)
    many = required_edge(500.0, 500, 3000.0, m)
    assert many.cost_dollars_per_month > few.cost_dollars_per_month


def test_required_edge_rejects_zero_trades():
    with pytest.raises(ValueError):
        required_edge(500.0, 0, 3000.0, CostModel())


def test_required_win_rate_above_one_is_reported_not_clamped():
    p = required_win_rate(gross_bps=50.0, win_bps=5.0, loss_bps=5.0)
    assert p > 1.0


def test_kelly_is_negative_for_a_losing_edge():
    assert kelly_fraction(0.40, 10.0, 10.0) < 0


def test_kelly_is_zero_at_break_even():
    assert kelly_fraction(0.50, 10.0, 10.0) == pytest.approx(0.0)


def test_risk_of_ruin_rises_as_win_rate_falls():
    hi = risk_of_ruin(0.60, 15, 15, 3000, 2400, 400, simulations=2000)
    lo = risk_of_ruin(0.40, 15, 15, 3000, 2400, 400, simulations=2000)
    assert lo > hi


def test_break_even_trades_infinite_when_edge_not_positive():
    assert break_even_trades(500.0, 0.0, 3000.0) == float("inf")


# -- strategy -------------------------------------------------------------

def _bars(prices, symbol="SPY", start_hour=14):
    ts = datetime(2026, 9, 1, start_hour, 0, tzinfo=timezone.utc)
    from datetime import timedelta
    return [Bar(ts=ts + timedelta(minutes=i), symbol=symbol, close=p)
            for i, p in enumerate(prices)]


def test_strategy_holds_while_warming_up():
    s = ScalpStrategy(StrategyParams(lookback=20))
    for b in _bars([100.0] * 5):
        assert s.on_bar(b).action is Action.HOLD


def test_strategy_enters_long_on_downside_extreme():
    p = StrategyParams(lookback=10, entry_z=1.5, trade_start="00:00", trade_end="23:59")
    s = ScalpStrategy(p)
    prices = [100.0 + (0.05 if i % 2 else -0.05) for i in range(10)] + [98.0]
    sig = None
    for b in _bars(prices):
        sig = s.on_bar(b)
    assert sig.action is Action.ENTER_LONG


def test_strategy_enters_short_on_upside_extreme():
    p = StrategyParams(lookback=10, entry_z=1.5, trade_start="00:00", trade_end="23:59")
    s = ScalpStrategy(p)
    prices = [100.0 + (0.05 if i % 2 else -0.05) for i in range(10)] + [102.0]
    sig = None
    for b in _bars(prices):
        sig = s.on_bar(b)
    assert sig.action is Action.ENTER_SHORT


def test_strategy_exits_when_outside_session():
    p = StrategyParams(lookback=5, trade_start="09:45", trade_end="15:50")
    s = ScalpStrategy(p)
    for b in _bars([100.0, 100.5, 99.5, 100.2, 99.8], start_hour=14):
        s.on_bar(b)
    s.mark_entry("SPY", Side.LONG, 100.0, -2.0)
    late = Bar(ts=datetime(2026, 9, 1, 23, 0, tzinfo=timezone.utc), symbol="SPY", close=100.0)
    assert s.on_bar(late).action is Action.EXIT


def test_strategy_respects_hold_limit():
    p = StrategyParams(lookback=5, hold_limit=3, exit_z=0.0, stop_z=99.0,
                       trade_start="00:00", trade_end="23:59")
    s = ScalpStrategy(p)
    for b in _bars([100.0, 101.0, 99.0, 100.5, 99.5]):
        s.on_bar(b)
    s.mark_entry("SPY", Side.LONG, 100.0, -2.0)
    actions = [s.on_bar(b).action for b in _bars([99.4, 99.3, 99.2, 99.1], start_hour=15)]
    assert Action.EXIT in actions


def test_cooldown_blocks_immediate_reentry():
    p = StrategyParams(lookback=5, entry_z=0.5, cooldown_bars=3,
                       trade_start="00:00", trade_end="23:59")
    s = ScalpStrategy(p)
    for b in _bars([100.0, 101.0, 99.0, 100.5, 99.5]):
        s.on_bar(b)
    s.mark_exit("SPY")
    sig = s.on_bar(Bar(ts=datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc),
                       symbol="SPY", close=90.0))
    assert sig.action is Action.HOLD and sig.reason == "cooldown"


# -- risk -----------------------------------------------------------------

def test_daily_loss_limit_halts():
    r = RiskManager(limits=RiskLimits(max_daily_loss=50.0), equity=3000.0)
    r.record_trade(-60.0)
    assert not r.can_enter(1000.0).allowed


def test_consecutive_losses_halt():
    r = RiskManager(limits=RiskLimits(max_consecutive_losses=3), equity=3000.0)
    for _ in range(3):
        r.record_trade(-1.0)
    assert "consecutive" in r.can_enter(100.0).reason


def test_a_win_resets_the_losing_streak():
    r = RiskManager(limits=RiskLimits(max_consecutive_losses=3), equity=3000.0)
    r.record_trade(-1.0)
    r.record_trade(-1.0)
    r.record_trade(5.0)
    assert r.consecutive_losses == 0
    assert r.can_enter(100.0).allowed


def test_equity_floor_halts():
    r = RiskManager(limits=RiskLimits(min_equity=2400.0), equity=2399.0)
    assert not r.can_enter(100.0).allowed


def test_new_day_clears_daily_halt_but_not_equity_halt():
    r = RiskManager(limits=RiskLimits(max_daily_loss=50.0), equity=3000.0)
    r.start_day(date(2026, 9, 1), 3000.0)
    r.record_trade(-60.0)
    r.can_enter(100.0)
    r.start_day(date(2026, 9, 2), 2940.0)
    assert r.can_enter(100.0).allowed


def test_trade_cap_blocks_without_permanent_halt():
    r = RiskManager(limits=RiskLimits(max_trades_per_day=2), equity=3000.0)
    r.record_trade(1.0)
    r.record_trade(1.0)
    d = r.can_enter(100.0)
    assert not d.allowed and not r.halted


def test_position_sizing_respects_notional_cap():
    cfg = Config(capital=3000.0)
    n = position_notional(cfg, 3000.0, 500.0, 650.0, stop_distance_bps=1.0)
    assert n == cfg.risk.max_position_notional


def test_position_sizing_honours_leverage_above_equity():
    """Regression: raw equity must not cap the notional below the position cap."""
    cfg = Config(capital=3000.0, risk=RiskLimits(max_position_notional=12_000.0,
                                                 max_trade_risk=60.0))
    n = position_notional(cfg, 3000.0, 500.0, 650.0, stop_distance_bps=1.0)
    assert n == 12_000.0


def test_position_sizing_zero_when_no_equity():
    cfg = Config(capital=3000.0)
    assert position_notional(cfg, 0.0, 500.0, 650.0, 20.0) == 0.0


# -- governor -------------------------------------------------------------

def test_governor_stops_at_target():
    g = ProfitGovernor(target=500.0)
    g.record(499.0)
    assert g.can_trade().allowed
    g.record(2.0, "2026-09-20")
    assert not g.can_trade().allowed
    assert g.reached_on == "2026-09-20"


def test_governor_remaining_never_negative():
    g = ProfitGovernor(target=500.0)
    g.record(600.0)
    assert g.remaining == 0.0


def test_governor_can_be_disabled():
    g = ProfitGovernor(target=500.0, enabled=False)
    g.record(5000.0)
    assert g.can_trade().allowed


def test_governor_reset_clears_state():
    g = ProfitGovernor(target=500.0)
    g.record(600.0, "2026-09-20")
    g.reset()
    assert g.realized == 0.0 and g.reached_on is None and g.can_trade().allowed


# -- backtest -------------------------------------------------------------

def test_random_walk_loses_approximately_its_costs():
    """No edge by construction: net must be negative and close to -costs."""
    cfg = Config.from_env()
    r = Backtester(cfg).run(SyntheticBars(bars=390 * 21, reversion=0.0, seed=11))
    assert r.net_pnl < 0
    assert r.total_costs > 0
    assert r.net_pnl == pytest.approx(r.gross_pnl - r.total_costs, abs=1e-6)


def test_injected_mean_reversion_is_detected():
    cfg = Config.from_env()
    r = Backtester(cfg).run(SyntheticBars(bars=390 * 21, reversion=0.30, seed=11))
    assert r.net_pnl > 0
    assert r.win_rate > 0.5


def test_backtest_net_equals_gross_minus_costs():
    cfg = Config.from_env()
    r = Backtester(cfg).run(SyntheticBars(bars=390 * 5, reversion=0.2, seed=3))
    for t in r.trades:
        assert t.net_pnl == pytest.approx(t.gross_pnl - t.costs)


def test_higher_frequency_erodes_net_edge_per_trade():
    """The premise under test: more volume does not mean more profit."""
    cfg = Config.from_env()
    slow = replace(cfg, strategy=replace(cfg.strategy, entry_z=2.0, lookback=20))
    fast = replace(cfg, strategy=replace(cfg.strategy, entry_z=0.75, lookback=5,
                                         cooldown_bars=0, hold_limit=5))
    rs = Backtester(slow).run(SyntheticBars(bars=390 * 21, reversion=0.30, seed=11))
    rf = Backtester(fast).run(SyntheticBars(bars=390 * 21, reversion=0.30, seed=11))
    assert len(rf.trades) > len(rs.trades)
    slow_bps = sum(t.net_pnl / t.notional for t in rs.trades) / len(rs.trades)
    fast_bps = sum(t.net_pnl / t.notional for t in rf.trades) / len(rf.trades)
    assert fast_bps < slow_bps


def test_governor_stops_the_backtest_at_target():
    cfg = replace(Config.from_env(), monthly_target=50.0)
    r = Backtester(cfg).run(SyntheticBars(bars=390 * 21, reversion=0.5, seed=11))
    assert r.target_reached_on is not None
    assert any("target" in h for h in r.halts)


# -- state and runner -----------------------------------------------------

def test_state_round_trips(tmp_path):
    p = tmp_path / "s.json"
    st = MonthState(path=p, period="2026-09", realized=123.45, equity=3123.45)
    st.save()
    back = MonthState.load(p, "2026-09", 3000.0)
    assert back.realized == 123.45 and back.equity == 3123.45


def test_new_month_resets_pnl_but_carries_equity(tmp_path):
    p = tmp_path / "s.json"
    MonthState(path=p, period="2026-09", realized=500.0, equity=3500.0).save()
    nxt = MonthState.load(p, "2026-10", 3000.0)
    assert nxt.realized == 0.0 and nxt.equity == 3500.0


def test_runner_records_trades_and_persists(tmp_path):
    cfg = replace(Config.from_env(), state_path=str(tmp_path / "s.json"))
    r = Runner(cfg)
    r.run_bars(SyntheticBars(bars=390 * 10, reversion=0.3, seed=5))
    assert len(r.state.trades) > 0
    assert Path(cfg.state_path).exists()
    reloaded = MonthState.load(cfg.state_path, r.state.period, cfg.capital)
    assert reloaded.realized == pytest.approx(r.state.realized, abs=0.01)


def test_runner_stops_once_target_reached(tmp_path):
    cfg = replace(Config.from_env(), state_path=str(tmp_path / "s.json"),
                  monthly_target=25.0)
    r = Runner(cfg)
    r.run_bars(SyntheticBars(bars=390 * 21, reversion=0.5, seed=5))
    assert r.state.realized >= 25.0
    assert not r.gov.can_trade().allowed


# -- safety ---------------------------------------------------------------

def test_broker_refuses_orders_without_both_flags(monkeypatch):
    from hft.broker import AlpacaBroker
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setenv("USE_ALPACA", "true")
    monkeypatch.setenv("HFT_LIVE_ORDERS", "false")
    b = AlpacaBroker(base_url="https://paper-api.alpaca.markets")
    with pytest.raises(PermissionError):
        b.submit("SPY", 1000.0, "buy")


def test_broker_requires_credentials(monkeypatch):
    from hft.broker import AlpacaBroker
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    with pytest.raises(ValueError):
        AlpacaBroker()


def test_market_data_requires_credentials(monkeypatch):
    from hft.marketdata import AlpacaBars
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    with pytest.raises(ValueError):
        AlpacaBars()


# -- regressions found by the first real-data backtest ---------------------

def test_consecutive_loss_halt_clears_on_a_new_day():
    """Regression: the halt was matched by reason-string prefix, so a
    consecutive-loss halt never cleared and the engine went idle forever."""
    from hft.risk import HaltScope
    r = RiskManager(limits=RiskLimits(max_consecutive_losses=3), equity=3000.0)
    r.start_day(date(2026, 6, 1), 3000.0)
    for _ in range(3):
        r.record_trade(-1.0)
    d = r.can_enter(100.0)
    assert not d.allowed and r.halt_scope is HaltScope.DAY
    r.start_day(date(2026, 6, 2), 2997.0)
    assert r.can_enter(100.0).allowed
    assert r.consecutive_losses == 0


def test_equity_halt_survives_a_new_day():
    from hft.risk import HaltScope
    r = RiskManager(limits=RiskLimits(min_equity=2400.0), equity=2399.0)
    r.can_enter(100.0)
    assert r.halt_scope is HaltScope.PERMANENT
    r.start_day(date(2026, 6, 2), 2399.0)
    assert not r.can_enter(100.0).allowed


def test_monthly_halt_survives_a_new_day_but_clears_on_a_new_month():
    from hft.risk import HaltScope
    r = RiskManager(limits=RiskLimits(max_monthly_loss=50.0), equity=3000.0)
    r.start_day(date(2026, 6, 1), 3000.0)
    r.record_trade(-60.0)
    r.can_enter(100.0)
    assert r.halt_scope is HaltScope.MONTH
    r.start_day(date(2026, 6, 2), 2940.0)
    assert not r.can_enter(100.0).allowed
    r.start_month()
    assert r.can_enter(100.0).allowed


def test_session_window_is_eastern_not_utc():
    """Regression: Alpaca stamps bars in UTC. Comparing them against an
    Eastern window shifted the trading day into the pre-market."""
    from datetime import timezone
    s = ScalpStrategy(StrategyParams(trade_start="09:45", trade_end="15:50"))
    # 14:00 UTC == 10:00 ET in June: inside the session.
    assert s.in_session(datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc))
    # 13:00 UTC == 09:00 ET: pre-market, outside the session.
    assert not s.in_session(datetime(2026, 6, 15, 13, 0, tzinfo=timezone.utc))
    # 20:30 UTC == 16:30 ET: after the close.
    assert not s.in_session(datetime(2026, 6, 15, 20, 30, tzinfo=timezone.utc))


def test_session_window_handles_daylight_saving_shift():
    from datetime import timezone
    s = ScalpStrategy(StrategyParams(trade_start="09:45", trade_end="15:50"))
    # January: ET is UTC-5, so 15:00 UTC == 10:00 ET.
    assert s.in_session(datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc))
    # July: ET is UTC-4, so 15:00 UTC == 11:00 ET, also in session,
    # but 14:00 UTC is 09:00 ET in January and 10:00 ET in July.
    assert not s.in_session(datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc))
    assert s.in_session(datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc))


def test_backtest_rolls_the_month():
    """A multi-month window must reset the monthly budget, or one bad month
    halts every month that follows."""
    from datetime import timedelta, timezone
    cfg = replace(Config.from_env(), monthly_target=1e9)  # governor never fires
    bars = list(SyntheticBars(bars=390 * 45, reversion=0.3, seed=9))
    # Stretch the synthetic bars across a real month boundary.
    base = datetime(2026, 6, 25, 14, 0, tzinfo=timezone.utc)
    for i, b in enumerate(bars):
        b.ts = base + timedelta(minutes=i)
    months = {(b.ts.year, b.ts.month) for b in bars}
    assert len(months) > 1, "fixture must span a month boundary"
    r = Backtester(cfg).run(bars)
    assert len(r.trades) > 0


# -- strategy search ------------------------------------------------------

def test_split_is_chronological_and_disjoint():
    from hft.search import split_bars
    bars = list(SyntheticBars(bars=390 * 30, seed=2))
    a, b = split_bars(bars, oos_fraction=0.3)
    assert a and b
    assert max(x.ts for x in a) < min(x.ts for x in b)
    assert len(a) + len(b) == len(bars)


def test_split_never_leaks_a_day_across_the_boundary():
    from hft.search import split_bars
    bars = list(SyntheticBars(bars=390 * 30, seed=2))
    a, b = split_bars(bars, oos_fraction=0.3)
    assert {x.ts.date() for x in a}.isdisjoint({x.ts.date() for x in b})


def test_research_evaluation_disables_the_profit_governor():
    """A governor halt would truncate the sample and bias every statistic."""
    from hft.search import Candidate, evaluate
    from hft.signals import VWAPReversion
    cfg = replace(Config.from_env(), monthly_target=1.0, stop_at_target=True)
    bars = list(SyntheticBars(bars=390 * 25, reversion=0.3, seed=4))
    ev = evaluate(cfg, bars, Candidate("vwap", {}, lambda: VWAPReversion(entry_bps=10.0)))
    assert ev.result.target_reached_on is None
    assert not any("target" in h for h in ev.result.halts)


def test_t_stat_is_zero_with_fewer_than_two_trades():
    """Undefined with n<2; must return 0 rather than divide by zero."""
    from hft.backtest import BacktestResult, ClosedTrade
    empty = BacktestResult()
    assert empty.t_stat == 0.0
    one = BacktestResult(trades=[ClosedTrade(
        symbol="SPY", side="long", entry_ts=None, exit_ts=None,
        entry_price=100.0, exit_price=101.0, notional=3000.0,
        gross_pnl=30.0, costs=1.0, reason="x")])
    assert one.t_stat == 0.0


def test_t_stat_sign_follows_mean_edge():
    cfg = Config.from_env()
    losing = Backtester(cfg).run(SyntheticBars(bars=390 * 21, reversion=0.0, seed=11))
    assert losing.t_stat < 0


def test_all_candidate_strategies_run_through_the_engine():
    from hft.signals import MomentumContinuation, OpeningRangeBreakout, VWAPReversion
    cfg = Config.from_env()
    bars = list(SyntheticBars(bars=390 * 20, reversion=0.1, seed=3))
    for factory in (OpeningRangeBreakout, VWAPReversion, MomentumContinuation):
        r = Backtester(cfg).run(bars, strategy=factory())
        assert r.bars_processed == len(bars)
        for t in r.trades:
            assert t.net_pnl == pytest.approx(t.gross_pnl - t.costs)


def test_orb_takes_at_most_one_trade_per_symbol_per_day():
    from hft.signals import OpeningRangeBreakout
    cfg = Config.from_env()
    bars = list(SyntheticBars(bars=390 * 20, reversion=0.0, seed=8))
    r = Backtester(cfg).run(bars, strategy=OpeningRangeBreakout())
    per_day = {}
    for t in r.trades:
        key = (t.symbol, t.entry_ts.date())
        per_day[key] = per_day.get(key, 0) + 1
    assert all(v == 1 for v in per_day.values())


def test_bar_fills_missing_ohlc_from_close():
    b = Bar(ts=datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc), symbol="SPY", close=650.0)
    assert b.open == b.high == b.low == 650.0


def test_search_limits_finalists_per_strategy_family():
    """Neighbouring params give near-identical results, so a plain top-N
    would fill every holdout slot with one idea under different labels."""
    from collections import Counter
    from hft.search import search
    cfg = Config.from_env()
    bars = list(SyntheticBars(bars=390 * 40, reversion=0.1, seed=5))
    out = search(cfg, bars, oos_fraction=0.3, min_trades=5, top_n=6,
                 max_per_family=2)
    counts = Counter(f["name"] for f in out["finalists"])
    assert counts and max(counts.values()) <= 2
