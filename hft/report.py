"""Monthly report — the single touchpoint."""

from __future__ import annotations

import json
from pathlib import Path

from hft.config import Config
from hft.state import MonthState


def build_report(cfg: Config, st: MonthState) -> str:
    n = len(st.trades)
    wins = sum(1 for t in st.trades if t.net > 0)
    gross = sum(t.gross for t in st.trades)
    costs = sum(t.costs for t in st.trades)
    avg_bps = (sum(t.net / t.notional for t in st.trades) / n * 10_000.0) if n else 0.0
    hit_target = st.realized >= cfg.monthly_target
    status = "TARGET REACHED" if hit_target else ("HALTED" if st.halted else "IN PROGRESS")

    lines = [
        f"# Trading report — {st.period}",
        "",
        f"**Status: {status}**",
        "",
        "## Cash",
        "",
        "| | Amount |",
        "|---|---:|",
        f"| Realized net profit this month | **${st.realized:,.2f}** |",
        f"| Monthly target | ${cfg.monthly_target:,.2f} |",
        f"| Account equity | ${st.equity:,.2f} |",
        f"| Starting capital | ${cfg.capital:,.2f} |",
        "",
    ]
    if hit_target:
        lines += [f"Target hit on {st.target_reached_on}. Trading is stopped until next month.",
                  f"Withdraw ${st.realized:,.2f} whenever you like.", ""]
    elif st.halted:
        lines += [f"Trading halted: {st.halt_reason}", ""]

    lines += [
        "## Execution",
        "",
        "| | Value |",
        "|---|---:|",
        f"| Trades closed | {n} |",
        f"| Win rate | {wins / n:.1%} |" if n else "| Win rate | n/a |",
        f"| Gross P&L | ${gross:,.2f} |",
        f"| Costs paid (spread, slippage, fees) | ${costs:,.2f} |",
        f"| Net P&L | ${st.realized:,.2f} |",
        f"| Average net edge per trade | {avg_bps:.2f} bps |",
        "",
    ]
    if costs > 0:
        lines += [f"Costs consumed {costs / max(abs(gross), 1e-9):.1%} of gross P&L.", ""]

    if st.trades:
        lines += ["## Last 10 trades", "",
                  "| Time | Symbol | Side | Notional | Net | Reason |",
                  "|---|---|---|---:|---:|---|"]
        for t in st.trades[-10:]:
            lines.append(f"| {t.ts[:19]} | {t.symbol} | {t.side} | ${t.notional:,.0f} "
                         f"| ${t.net:+,.2f} | {t.reason} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def write_report(cfg: Config, st: MonthState, out_dir: str | Path = "reports") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md = out / f"{st.period}.md"
    md.write_text(build_report(cfg, st))
    (out / f"{st.period}.json").write_text(json.dumps({
        "period": st.period, "realized": st.realized, "equity": st.equity,
        "trades": len(st.trades), "halted": st.halted,
        "halt_reason": st.halt_reason, "target_reached_on": st.target_reached_on,
    }, indent=2) + "\n")
    return md
