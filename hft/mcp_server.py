"""MCP server exposing the research harness to LLM clients.

    python -m hft.mcp_server                          # stdio, for Claude Code / Desktop
    python -m hft.mcp_server --http --port 8000       # Streamable HTTP at /mcp

Every tool is research-only. This module never imports hft.broker, so nothing
reachable through it can place an order, whatever USE_ALPACA and
HFT_LIVE_ORDERS say. The real-data tool reads Alpaca market data and nothing
else.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from hft.backtest import Backtester
from hft.config import Config
from hft.costs import round_trip_cost
from hft.feasibility import (annualized_from_monthly, kelly_fraction, required_edge,
                             required_win_rate, risk_of_ruin)
from hft.marketdata import SyntheticBars

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
MAX_SYNTHETIC_BARS = 390 * 252  # one trading year of minutes
MAX_RUIN_WORK = 20_000_000      # simulations * trades, keeps a call under a few seconds

INSTRUCTIONS = """\
Cost-accurate research harness for retail intraday strategies on liquid US ETFs.
Every fill pays half the spread each way, slippage, SEC Section 31 and FINRA TAF.
Judge strategies on avg_net_bps_per_trade and t_stat, never on net_pnl alone:
a positive result on few trades is luck. Synthetic backtests with reversion=0 are
a random walk, so their expected result is a loss equal to costs. Read
findings://summary before proposing a strategy; three families have already
failed out of sample. No tool here can place an order."""

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                            idempotent_hint=True, open_world_hint=False)

mcp = MCPServer("hft-research", instructions=INSTRUCTIONS)


def _cfg(capital: float | None = None, monthly_target: float | None = None,
         spread_bps: float | None = None, slippage_bps: float | None = None) -> Config:
    """Environment config with per-call overrides layered on top."""
    cfg = Config.from_env()
    if capital is not None:
        # Risk limits are derived from capital, so rebuild them at the new size.
        cfg = replace(cfg, capital=capital, risk=replace(
            cfg.risk, max_daily_loss=0.03 * capital, max_monthly_loss=0.10 * capital,
            max_trade_risk=0.005 * capital, max_position_notional=capital,
            min_equity=0.80 * capital))
    if monthly_target is not None:
        cfg = replace(cfg, monthly_target=monthly_target)
    costs = {k: v for k, v in (("spread_bps", spread_bps), ("slippage_bps", slippage_bps))
             if v is not None}
    if costs:
        cfg = replace(cfg, costs=replace(cfg.costs, **costs))
    return cfg


Positive = Annotated[float, Field(gt=0)]


@mcp.tool(annotations=READ_ONLY)
def round_trip_costs(
    notional: Annotated[float, Field(gt=0, description="Position size in dollars")] = 3000.0,
    price: Annotated[float, Field(gt=0, description="Share price, sets share count for FINRA TAF")] = 650.0,
    spread_bps: Annotated[float | None, Field(ge=0, description="Full quoted spread; default from config")] = None,
    slippage_bps: Annotated[float | None, Field(ge=0, description="Extra adverse fill per side")] = None,
) -> dict[str, Any]:
    """Break down what one round trip (buy then sell) costs, in dollars and basis points."""
    cfg = _cfg(spread_bps=spread_bps, slippage_bps=slippage_bps)
    c = round_trip_cost(cfg.costs, notional, price)
    out = c.as_dict()
    out["notional"] = notional
    out["round_trip_bps"] = round(c.total / notional * 10_000.0, 3)
    return out


@mcp.tool(annotations=READ_ONLY)
def feasibility(
    trades_per_month: Annotated[int, Field(gt=0, description="Planned round trips per month")] = 420,
    capital: Annotated[float | None, Field(gt=0, description="Account size; default from config")] = None,
    monthly_target: Annotated[float | None, Field(gt=0, description="Dollar profit goal per month")] = None,
    price: Positive = 650.0,
    win_loss_bps: Annotated[float, Field(gt=0, description="Symmetric win/loss size for the win-rate estimate")] = 10.0,
) -> dict[str, Any]:
    """Gross edge per trade and win rate a monthly profit target demands, after all costs.

    Notional per trade equals capital (no leverage), matching the default risk limits.
    """
    cfg = _cfg(capital=capital, monthly_target=monthly_target)
    r = required_edge(cfg.monthly_target, trades_per_month, cfg.capital, cfg.costs, price)
    p = required_win_rate(r.required_gross_bps, win_loss_bps, win_loss_bps)
    monthly = cfg.monthly_target / cfg.capital
    return {
        "capital": cfg.capital,
        "monthly_target": cfg.monthly_target,
        "required_monthly_return": round(monthly, 4),
        "required_annualized_return": round(annualized_from_monthly(monthly), 4),
        **r.as_dict(),
        "win_loss_bps": win_loss_bps,
        "required_win_rate": round(p, 4),
        "win_rate_verdict": "impossible" if p > 1.0 else ("implausible" if p > 0.75 else "plausible"),
    }


@mcp.tool(annotations=READ_ONLY)
def ruin_probability(
    win_rate: Annotated[float, Field(ge=0, le=1)],
    trades: Annotated[int, Field(gt=0, le=10_000, description="Trades simulated per path")] = 420,
    payoff: Annotated[float, Field(gt=0, description="Win size as a multiple of per-trade risk")] = 1.0,
    capital: Annotated[float | None, Field(gt=0)] = None,
    simulations: Annotated[int, Field(ge=100, le=50_000)] = 20_000,
) -> dict[str, Any]:
    """Monte Carlo chance of hitting the equity floor, with Kelly fraction and expectancy.

    Trades are i.i.d.; real losing streaks cluster, so treat this as a lower bound.
    """
    if simulations * trades > MAX_RUIN_WORK:
        raise ToolError(f"simulations * trades must be <= {MAX_RUIN_WORK:,}")
    cfg = _cfg(capital=capital)
    loss = cfg.risk.max_trade_risk
    win = loss * payoff
    return {
        "capital": cfg.capital,
        "ruin_level": cfg.risk.min_equity,
        "risk_per_trade": loss,
        "win_per_trade": win,
        "probability_of_ruin": risk_of_ruin(win_rate, win, loss, cfg.capital,
                                            cfg.risk.min_equity, trades, simulations),
        "kelly_fraction": round(kelly_fraction(win_rate, win, loss), 4),
        "expectancy_per_trade": round(win_rate * win - (1 - win_rate) * loss, 4),
    }


@mcp.tool(annotations=READ_ONLY)
def synthetic_backtest(
    bars: Annotated[int, Field(ge=100, le=MAX_SYNTHETIC_BARS, description="Minute bars; 8190 is one month")] = 390 * 21,
    reversion: Annotated[float, Field(ge=0, le=1, description="Injected mean reversion; 0 is a random walk")] = 0.0,
    vol_bps: Annotated[float, Field(gt=0, description="Per-bar volatility")] = 4.0,
    seed: int = 11,
    entry_z: Annotated[float | None, Field(gt=0)] = None,
    exit_z: Annotated[float | None, Field(ge=0)] = None,
    stop_z: Annotated[float | None, Field(gt=0)] = None,
    lookback: Annotated[int | None, Field(ge=2)] = None,
    hold_limit: Annotated[int | None, Field(ge=1)] = None,
    capital: Annotated[float | None, Field(gt=0)] = None,
) -> dict[str, Any]:
    """Run the z-score mean-reversion strategy on generated bars with full costs and risk limits.

    Useful for asking how much edge the market would need to contain. It is not
    evidence about real markets; use real_backtest for that.
    """
    cfg = _cfg(capital=capital)
    overrides = {k: v for k, v in (("entry_z", entry_z), ("exit_z", exit_z), ("stop_z", stop_z),
                                   ("lookback", lookback), ("hold_limit", hold_limit))
                 if v is not None}
    if overrides:
        cfg = replace(cfg, strategy=replace(cfg.strategy, **overrides))
    res = Backtester(cfg).run(SyntheticBars(bars=bars, reversion=reversion, seed=seed,
                                            vol_bps=vol_bps))
    out = res.summary(cfg.capital)
    out["strategy"] = {k: v for k, v in asdict(cfg.strategy).items() if k != "symbols"}
    if reversion == 0.0:
        out["note"] = ("reversion=0 is a pure random walk: any profit is noise and the "
                       "expected result is a loss equal to the costs paid.")
    return out


def verdict(summary: dict[str, Any], cfg: Config, price: float) -> str:
    """Same judgement as `hft.cli realtest`, as one string."""
    n, avg = summary["trades"], summary["avg_net_bps_per_trade"]
    if n < 100:
        return f"{n} trades is too few to conclude anything. Widen the window."
    if avg <= 0:
        return (f"Average net edge is {avg:+.2f} bps per trade: the strategy loses money "
                "after costs on this data. Do not fund this.")
    needed = required_edge(cfg.monthly_target, n, cfg.capital, cfg.costs, price).required_net_bps
    if avg >= needed:
        return (f"{avg:+.2f} bps/trade over {n} trades clears the {needed:.2f} bps the "
                "target needs on this window. Confirm on a paper month before funding.")
    return (f"{avg:+.2f} bps/trade over {n} trades is positive but {needed - avg:.2f} bps "
            "short of what the target needs.")


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                      idempotent_hint=True, open_world_hint=True))
def real_backtest(
    start: Annotated[str, Field(description="ISO date, e.g. 2026-06-01")],
    end: Annotated[str, Field(description="ISO date, at least 15 minutes in the past for SIP")],
    symbols: Annotated[list[str] | None, Field(description="Default from HFT_SYMBOLS")] = None,
    feed: Annotated[str, Field(pattern="^(sip|iex)$")] = "sip",
    price: Positive = 650.0,
) -> dict[str, Any]:
    """Backtest on real Alpaca minute bars and return a verdict.

    Reads market data only. Needs ALPACA_API_KEY and ALPACA_SECRET_KEY in the
    server's environment. Long windows fetch many pages and can take minutes.
    """
    from hft.marketdata import AlpacaBars

    cfg = Config.from_env()
    if symbols:
        cfg = replace(cfg, strategy=replace(
            cfg.strategy, symbols=tuple(s.strip().upper() for s in symbols if s.strip())))
    try:
        source = AlpacaBars(feed=feed)
    except ValueError as e:
        raise ToolError(f"{e}. Set them in the MCP server's environment.") from e

    counts, bars = {}, []
    for sym in cfg.strategy.symbols:
        try:
            got = source.history(sym, start=start, end=end)
        except Exception as e:  # network or entitlement errors come back as text
            raise ToolError(f"fetching {sym} failed: {type(e).__name__}: {e}") from e
        counts[sym] = len(got)
        bars.extend(got)
    if not bars:
        raise ToolError("No bars returned. Check the date window and your data entitlement.")
    bars.sort(key=lambda b: b.ts)

    summary = Backtester(cfg).run(bars).summary(cfg.capital)
    summary.update(feed=feed, window=f"{start}..{end}", bars_by_symbol=counts,
                   verdict=verdict(summary, cfg, price))
    return summary


@mcp.resource("findings://summary", mime_type="text/markdown")
def findings() -> str:
    """Every strategy tested so far, with in-sample and out-of-sample results."""
    return (ROOT / "FINDINGS.md").read_text()


@mcp.resource("findings://research-plan", mime_type="text/markdown")
def research_plan() -> str:
    """What to test next and why."""
    return (ROOT / "RESEARCH_PLAN.md").read_text()


@mcp.resource("results://index", mime_type="application/json")
def results_index() -> str:
    """Names of recorded result write-ups, readable at results://{name}."""
    return json.dumps(sorted(p.stem for p in RESULTS.glob("*.md")))


@mcp.resource("results://{name}", mime_type="text/markdown")
def result(name: str) -> str:
    """One recorded result write-up from results/."""
    path = (RESULTS / f"{name}.md").resolve()
    if path.parent != RESULTS.resolve() or not path.is_file():
        raise ValueError(f"no result named {name!r}; see results://index")
    return path.read_text()


@mcp.resource("config://current", mime_type="application/json")
def current_config() -> str:
    """Capital, target, cost model, strategy and risk limits the tools use by default."""
    return json.dumps(asdict(Config.from_env()), indent=2)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="hft-mcp", description=__doc__.splitlines()[0])
    p.add_argument("--http", action="store_true", help="serve Streamable HTTP instead of stdio")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)
    if args.http:
        mcp.run("streamable-http", host=args.host, port=args.port)
    else:
        mcp.run("stdio")


if __name__ == "__main__":
    main()
