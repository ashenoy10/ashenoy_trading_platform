"""Monthly performance report (markdown + json)."""

from __future__ import annotations

import json
from pathlib import Path

from income.config import IncomeConfig
from income.engine import CycleResult
from income.ledger import Ledger
from income.sizing import monthly_income, months_of_cover


def build_report(cfg: IncomeConfig, ledger: Ledger, result: CycleResult) -> str:
    r = result.record
    a = result.after
    forecast = monthly_income(a.principal_value, cfg.planning_yield)
    cover = months_of_cover(r.reserve_after, a.principal_value, cfg.planning_yield, cfg.target_monthly)
    cover_txt = "not needed (income covers target)" if cover is None else f"{cover:.1f} months"
    status = "ON TARGET" if abs(r.payout - cfg.target_monthly) <= cfg.tolerance else "OFF TARGET"

    lines = [
        f"# Income report — {r.period}",
        "",
        f"**Status: {status}** · mode: `{r.mode}` · run: {r.run_at[:19]}Z",
        "",
        "## Cash for you this month",
        "",
        "| | Amount |",
        "|---|---:|",
        f"| Payout available to withdraw | **${r.payout:,.2f}** |",
        f"| Total un-withdrawn cash in account (excl. reserve) | ${result.unwithdrawn_cash:,.2f} |",
        "",
        "Withdraw from the Alpaca dashboard (ACH, no fee). Leave the reserve in place.",
        "",
        "## What happened",
        "",
        "| | Amount |",
        "|---|---:|",
        f"| Income received ({cfg.instrument} distributions) | ${r.income_received:,.2f} |",
        f"| Reserve before → after | ${r.reserve_before:,.2f} → ${r.reserve_after:,.2f} |",
        f"| Surplus reinvested | ${r.reinvested:,.2f} |",
        f"| Principal sold to cover shortfall | ${r.principal_sold:,.2f} |",
        "",
        "## Portfolio",
        "",
        "| | Value |",
        "|---|---:|",
        f"| {cfg.instrument} shares | {a.shares:,.4f} @ ${a.price:,.2f} |",
        f"| Principal value | ${a.principal_value:,.2f} |",
        f"| Cash (payout + reserve) | ${a.cash:,.2f} |",
        f"| Total account value | ${a.total:,.2f} |",
        "",
        "## Forecast",
        "",
        f"- Next month's expected income at {cfg.planning_yield:.2%} planning yield: ${forecast:,.2f}",
        f"- Reserve cover if income stays there: {cover_txt}",
        f"- Lifetime paid out: ${ledger.total_paid():,.2f} over {len(ledger.cycles)} cycle(s)",
    ]
    actions = [n for n in r.notes if n.startswith("ACTION") or n.startswith("PRINCIPAL")]
    other = [n for n in r.notes if n not in actions]
    if actions:
        lines += ["", "## Action required", ""] + [f"- **{n}**" for n in actions]
    if other:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in other]
    if not r.notes:
        lines += ["", "No action needed. Withdraw the payout whenever you like."]
    return "\n".join(lines) + "\n"


def write_report(cfg: IncomeConfig, ledger: Ledger, result: CycleResult, out_dir: str | Path = "reports/monthly") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md = out / f"{result.record.period}.md"
    md.write_text(build_report(cfg, ledger, result))
    (out / f"{result.record.period}.json").write_text(
        json.dumps(
            {
                "record": result.record.__dict__,
                "after": result.after.__dict__,
                "unwithdrawn_cash": result.unwithdrawn_cash,
            },
            indent=2,
        )
        + "\n"
    )
    return md
