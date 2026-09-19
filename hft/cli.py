"""Entry point.

    python -m hft.cli feasibility        # what edge the target demands
    python -m hft.cli costs              # round-trip cost breakdown
    python -m hft.cli backtest           # run the strategy with full costs
    python -m hft.cli sweep              # edge needed to actually reach $500
    python -m hft.cli ruin               # probability of losing the account
    python -m hft.cli latency            # measure broker round-trip time
    python -m hft.cli report             # rebuild the monthly report
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from hft.backtest import Backtester
from hft.config import Config
from hft.costs import round_trip_cost
from hft.feasibility import (annualized_from_monthly, kelly_fraction, required_edge,
                             required_win_rate, risk_of_ruin)
from hft.marketdata import SyntheticBars
from hft.report import build_report, write_report
from hft.state import MonthState


def cmd_costs(cfg: Config, args) -> int:
    c = round_trip_cost(cfg.costs, args.notional, args.price)
    out = c.as_dict()
    out["notional"] = args.notional
    out["round_trip_bps"] = round(c.total / args.notional * 10_000.0, 3)
    print(json.dumps(out, indent=2))
    return 0


def cmd_feasibility(cfg: Config, args) -> int:
    monthly = cfg.monthly_target / cfg.capital
    print(f"Capital ${cfg.capital:,.0f}  target ${cfg.monthly_target:,.0f}/month")
    print(f"Required return: {monthly:.2%} per month = {annualized_from_monthly(monthly):,.1%} annualized\n")
    print(f"{'trades/mo':>10} {'cost bps':>9} {'need gross bps':>15} {'$/trade':>9} {'costs/mo':>10}")
    for n in (20, 50, 100, 250, 420, 1000, 2000):
        r = required_edge(cfg.monthly_target, n, cfg.capital, cfg.costs, args.price)
        print(f"{n:>10} {r.cost_bps:>9.2f} {r.required_gross_bps:>15.2f} "
              f"{r.required_gross_dollars:>9.2f} {r.cost_dollars_per_month:>10.2f}")
    print("\nWin rate needed, assuming symmetric win/loss of the stated size:")
    r = required_edge(cfg.monthly_target, args.trades, cfg.capital, cfg.costs, args.price)
    for size in (5.0, 10.0, 20.0, 40.0):
        p = required_win_rate(r.required_gross_bps, size, size)
        verdict = "impossible" if p > 1.0 else ("implausible" if p > 0.75 else "plausible")
        print(f"  ±{size:>5.1f} bps per trade -> {p:>7.1%} win rate   {verdict}")
    return 0


def cmd_backtest(cfg: Config, args) -> int:
    bars = SyntheticBars(bars=args.bars, reversion=args.reversion, seed=args.seed,
                         vol_bps=args.vol)
    res = Backtester(cfg).run(bars)
    print(json.dumps(res.summary(cfg.capital), indent=2))
    if args.reversion == 0.0:
        print("\nNote: reversion=0 is a pure random walk. Any profit here is noise,")
        print("and the expected result is a loss equal to the costs paid.")
    return 0


def cmd_sweep(cfg: Config, args) -> int:
    print("How much mean reversion the market would have to contain for this")
    print(f"strategy to clear ${cfg.monthly_target:,.0f}/month on ${cfg.capital:,.0f}:\n")
    print(f"{'reversion':>10} {'trades':>7} {'gross':>9} {'costs':>8} {'net':>9} {'bps/trade':>10}")
    for rev in (0.0, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0):
        bars = SyntheticBars(bars=args.bars, reversion=rev, seed=args.seed)
        r = Backtester(cfg).run(bars)
        avg = (sum(t.net_pnl / t.notional for t in r.trades) / len(r.trades) * 10_000.0
               if r.trades else 0.0)
        flag = "  <== clears target" if r.net_pnl >= cfg.monthly_target else ""
        print(f"{rev:>10.2f} {len(r.trades):>7} {r.gross_pnl:>9.2f} {r.total_costs:>8.2f} "
              f"{r.net_pnl:>9.2f} {avg:>10.2f}{flag}")
    print("\nReal equity index returns show very little minute-scale mean reversion.")
    print("Treat the reversion column as 'how generous would reality have to be'.")
    return 0


def cmd_frequency(cfg: Config, args) -> int:
    """Trade more often and watch net edge per trade fall."""
    from dataclasses import replace
    print("Loosening the entry threshold to trade more often, data and edge held fixed.")
    print(f"Injected reversion={args.reversion} (generous). Target ${cfg.monthly_target:,.0f}.\n")
    print(f"{'entry_z':>8} {'lookback':>9} {'trades':>7} {'gross':>9} {'costs':>8} "
          f"{'net':>9} {'net bps':>8}")
    rows = []
    for ez in (0.75, 1.0, 1.5, 2.0):
        for lb in (5, 10, 20):
            c = replace(cfg, strategy=replace(
                cfg.strategy, entry_z=ez, lookback=lb, exit_z=0.2,
                stop_z=ez + 1.5, hold_limit=5, cooldown_bars=0))
            r = Backtester(c).run(SyntheticBars(bars=args.bars, reversion=args.reversion,
                                                seed=args.seed))
            avg = (sum(t.net_pnl / t.notional for t in r.trades) / len(r.trades) * 10_000.0
                   if r.trades else 0.0)
            rows.append((len(r.trades), avg))
            print(f"{ez:>8.2f} {lb:>9} {len(r.trades):>7} {r.gross_pnl:>9.2f} "
                  f"{r.total_costs:>8.2f} {r.net_pnl:>9.2f} {avg:>8.2f}")
    active = [r for r in rows if r[0] > 0] or rows
    busiest = max(active, key=lambda x: x[0])
    quietest = min(active, key=lambda x: x[0])
    print(f"\nBusiest config: {busiest[0]} trades at {busiest[1]:.2f} net bps each.")
    print(f"Quietest config: {quietest[0]} trades at {quietest[1]:.2f} net bps each.")
    print("Net edge per trade falls as frequency rises. That is the cost base eating it.")
    return 0


def cmd_leverage(cfg: Config, args) -> int:
    """Leverage multiplies edge in both directions."""
    from dataclasses import replace
    tuned = replace(cfg.strategy, entry_z=1.0, lookback=20, exit_z=0.2,
                    stop_z=2.5, hold_limit=5, cooldown_bars=0)
    print(f"Tuned params, target ${cfg.monthly_target:,.0f}, capital ${cfg.capital:,.0f}\n")
    print(f"{'leverage':>9} {'position cap':>13} {'net (edge)':>12} {'net (no edge)':>14} "
          f"{'maxDD':>9}")
    for lev in (1, 2, 3, 4):
        c = replace(cfg, strategy=tuned, risk=replace(
            cfg.risk,
            max_position_notional=cfg.capital * lev,
            max_trade_risk=cfg.risk.max_trade_risk * lev,
            max_daily_loss=cfg.risk.max_daily_loss * lev,
            max_monthly_loss=cfg.risk.max_monthly_loss * lev))
        edge = Backtester(c).run(SyntheticBars(bars=args.bars, reversion=args.reversion,
                                               seed=args.seed))
        none = Backtester(c).run(SyntheticBars(bars=args.bars, reversion=0.0, seed=args.seed))
        flag = "  <== clears target" if edge.net_pnl >= cfg.monthly_target else ""
        print(f"{lev:>9}x {cfg.capital * lev:>13,.0f} {edge.net_pnl:>12,.2f} "
              f"{none.net_pnl:>14,.2f} {edge.max_drawdown:>9,.2f}{flag}")
    print("\nThe 'no edge' column is the same leverage applied to a random walk.")
    print("Leverage scales whatever edge you have, including a negative one.")
    return 0


def cmd_ruin(cfg: Config, args) -> int:
    risk = cfg.risk.max_trade_risk
    win = risk * args.payoff
    print(f"Per-trade risk ${risk:,.2f}, win ${win:,.2f}, capital ${cfg.capital:,.0f}")
    print(f"Ruin level ${cfg.risk.min_equity:,.2f} ({cfg.risk.min_equity / cfg.capital:.0%} of capital)\n")
    print(f"{'win rate':>9} {'P(hit floor)':>14} {'kelly':>8} {'expectancy/trade':>18}")
    for p in (0.40, 0.45, 0.50, 0.52, 0.55, 0.60, 0.65):
        pr = risk_of_ruin(p, win, risk, cfg.capital, cfg.risk.min_equity, args.trades)
        k = kelly_fraction(p, win, risk)
        ev = p * win - (1 - p) * risk
        print(f"{p:>9.0%} {pr:>14.1%} {k:>8.2f} {ev:>18.3f}")
    return 0


def cmd_latency(cfg: Config, args) -> int:
    from hft.broker import AlpacaBroker
    try:
        b = AlpacaBroker()
        print(json.dumps(b.measure_latency(args.samples), indent=2))
    except Exception as e:
        print(f"Could not measure latency: {type(e).__name__}: {e}")
        return 1
    return 0


def cmd_report(cfg: Config, args) -> int:
    period = args.period or datetime.now(timezone.utc).strftime("%Y-%m")
    st = MonthState.load(cfg.state_path, period, cfg.capital)
    print(build_report(cfg, st))
    print(f"report written: {write_report(cfg, st)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    cfg = Config.from_env()
    p = argparse.ArgumentParser(prog="hft", description="Capped-profit scalping platform")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("costs", help="round-trip cost breakdown")
    s.add_argument("--notional", type=float, default=3000.0)
    s.add_argument("--price", type=float, default=650.0)
    s.set_defaults(func=cmd_costs)

    s = sub.add_parser("feasibility", help="edge the target demands")
    s.add_argument("--price", type=float, default=650.0)
    s.add_argument("--trades", type=int, default=420)
    s.set_defaults(func=cmd_feasibility)

    s = sub.add_parser("backtest", help="run the strategy with full costs")
    s.add_argument("--bars", type=int, default=390 * 21)
    s.add_argument("--reversion", type=float, default=0.0)
    s.add_argument("--vol", type=float, default=4.0)
    s.add_argument("--seed", type=int, default=11)
    s.set_defaults(func=cmd_backtest)

    s = sub.add_parser("sweep", help="edge required to reach the target")
    s.add_argument("--bars", type=int, default=390 * 21)
    s.add_argument("--seed", type=int, default=11)
    s.set_defaults(func=cmd_sweep)

    s = sub.add_parser("frequency", help="show net edge per trade falling with volume")
    s.add_argument("--bars", type=int, default=390 * 21)
    s.add_argument("--reversion", type=float, default=0.30)
    s.add_argument("--seed", type=int, default=11)
    s.set_defaults(func=cmd_frequency)

    s = sub.add_parser("leverage", help="leverage sensitivity, with and without edge")
    s.add_argument("--bars", type=int, default=390 * 21)
    s.add_argument("--reversion", type=float, default=0.30)
    s.add_argument("--seed", type=int, default=11)
    s.set_defaults(func=cmd_leverage)

    s = sub.add_parser("ruin", help="probability of losing the account")
    s.add_argument("--trades", type=int, default=420)
    s.add_argument("--payoff", type=float, default=1.0)
    s.set_defaults(func=cmd_ruin)

    s = sub.add_parser("latency", help="measure broker round-trip latency")
    s.add_argument("--samples", type=int, default=5)
    s.set_defaults(func=cmd_latency)

    s = sub.add_parser("report", help="rebuild the monthly report")
    s.add_argument("--period", default=None)
    s.set_defaults(func=cmd_report)

    args = p.parse_args(argv)
    return args.func(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
