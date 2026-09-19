# hft — a cost-accurate research harness for retail intraday strategies

Most retail backtests are optimistic because they fill at mid price, ignore
regulatory fees, evaluate a strategy on the same data that chose it, and rank
candidates by total profit. Each of those turns a losing strategy into a
winning chart. This harness removes all four, then tells you plainly when an
idea does not work.

It is deliberately built to produce negative results. Every strategy tested in
it so far has failed, and the [results](results/) directory records exactly
how. That is the harness working, not the harness broken.

```
pip install -r requirements.txt
python -m pytest tests -q          # 59 tests
python -m hft.cli feasibility      # what a given target demands
```

---

## What it does differently

**Every fill pays what the market charges.** Half the quoted spread each way,
slippage, SEC Section 31 at $20.60 per $1M on sells, and the FINRA Trading
Activity Fee with its per-trade cap. On $3,000 of a liquid ETF that is 2.71
basis points per round trip. Strategies that look profitable on mid prices
usually die here, which is the point.

**Selection and validation use different data.** `hft.search` splits the
window chronologically, fits and ranks every candidate on the earlier portion,
and leaves the later holdout untouched until finalists are chosen. Days never
straddle the boundary, and the split is never random, because shuffling time
series leaks the future into the past.

**Ranking is by t-statistic, not profit.** A configuration that made money on
six lucky trades cannot outrank a steadier one. The harness also reports how
many configurations were tried, because the best of K always looks good
in-sample.

**The verdict logic refuses rather than rationalises.** If the best finalist
loses out of sample, it says the in-sample result was curve fit. If it wins
but the t-statistic is under 2, it says that is indistinguishable from luck.
Only a positive holdout edge with enough trades behind it gets a green light,
and even then it recommends paper trading first.

**Risk limits are enforced in the backtest, not bolted on later.** Daily and
monthly loss limits, per-trade risk budget, consecutive-loss and trade-count
caps, and a hard equity floor all run in the same code path the live runner
uses, so a backtest cannot show returns the live system would have halted.

---

## Commands

| Command | What it does |
|---|---|
| `feasibility` | Gross edge and win rate a target demands, by trade count |
| `costs` | Round-trip cost breakdown for one position |
| `backtest` | Run a strategy on synthetic bars with full costs |
| `realtest` | Run on real Alpaca minute bars, with a verdict |
| `search` | Search all candidates with an out-of-sample holdout |
| `frequency` | Net edge per trade as trade frequency rises |
| `leverage` | Leverage sensitivity, with and without edge |
| `ruin` | Monte Carlo probability of hitting the equity floor |
| `sweep` | How much edge the market would need to contain |
| `latency` | Measure broker round-trip time |
| `report` | Rebuild the monthly report |

Historical SIP data is free on Alpaca's Basic plan for any window ending more
than 15 minutes in the past, so research costs nothing. Only real-time SIP
needs the $99/month Algo Trader Plus plan.

---

## Layout

```
hft/
  config.py       capital, target, risk limits, cost model, strategy params
  costs.py        per-fill spread, slippage, SEC Section 31, FINRA TAF
  feasibility.py  required edge, required win rate, Kelly, risk of ruin
  strategy.py     z-score mean reversion, and the Bar/Signal types
  signals.py      opening range breakout, VWAP reversion, momentum
  search.py       grid search with chronological holdout and t-ranking
  backtest.py     event-driven, full costs, same control path as live
  risk.py         hard limits, halt scopes, and the profit governor
  marketdata.py   Alpaca bars plus a synthetic generator with tunable edge
  broker.py       Alpaca REST execution, double-guarded, latency probe
  runner.py       live/paper loop, persists every closed trade
  state.py        month state, committed to git
  report.py       monthly report
  cli.py          entry point
results/          recorded findings, including every negative result
```

### Adding a strategy

Implement `on_bar(bar) -> Signal`, `mark_entry`, `mark_exit`, and optionally
`stop_bps_for(bar)` to declare your own stop distance for position sizing. Add
it to the grid in `hft/search.py`. The backtester drives any object with that
shape, so a new idea needs no engine changes.

---

## Safety

No order reaches a broker unless `USE_ALPACA` and `HFT_LIVE_ORDERS` are both
exactly `true`, including against the paper endpoint. Two tests enforce it.
The default configuration is simulated, and credentials come from the
environment, never from a committed file.

---

## Findings so far

Three structurally different strategy families, 126 configurations, 20 months
of real SPY and QQQ minute bars, validated on an untouched 6-month holdout.
Every finalist loses out of sample.

| Strategy | In-sample | Out-of-sample | OOS t |
|---|---:|---:|---:|
| Opening range breakout | +4.71 bps | -13.54 bps | -3.53 |
| Momentum | +0.21 bps | -7.73 bps | -3.33 |
| VWAP reversion | -1.87 bps | -0.37 bps | -0.13 |

The informative result is the cost decomposition. Breakout and momentum are
directionally wrong and lose before costs apply. VWAP reversion carries a real
**+2.34 bps gross edge that the 2.71 bps round-trip cost consumes entirely**.
The intraday reversion effect exists and has been arbitraged down to almost
exactly the level of the toll required to harvest it.

Edge measured in basis points does not scale with account size, so this is not
a capital problem. A +2.34 bps edge against a 2.71 bps cost is negative at
$3,000 and equally negative at $300,000.

Full write-ups: [FINDINGS.md](FINDINGS.md) and [results/](results/).
Where the research goes next: [RESEARCH_PLAN.md](RESEARCH_PLAN.md).
Running it against a live account: [OPERATIONS.md](OPERATIONS.md).

---

## Running research

Two workflows run against real data on GitHub Actions, driven by committed
request files so every run leaves an auditable record of its parameters:

- `real-backtest.yml` — single strategy, edit `backtest-request.json`
- `strategy-search.yml` — full search, edit `search-request.json`

Both need `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` as repository secrets. Use
paper-account keys: market data plans apply to paper and live accounts alike,
and research never places an order.

## License

MIT. See [LICENSE](LICENSE).
