# Scalping platform — $3,000 capital, $500/month cap

A high-frequency mean-reversion scalper for liquid US equity ETFs, with two
things most retail trading code leaves out: a cost model that charges every
fill what the market actually charges, and a profit governor that stops
trading for the month the moment $500 is realized.

**Read [FINDINGS.md](FINDINGS.md) before funding anything.** The platform is
built and tested, but the arithmetic of the target is not favourable, and the
specific mechanism you proposed — volume compensating for a small per-trade
edge — works against you rather than for you. That document shows the numbers.

---

## The short version

$500/month on $3,000 is **16.7% per month, 536% annualized**. The costs are
small but they are charged per trade, so raising the trade count raises total
cost in direct proportion:

| Trades/month | Cost/trade | Gross edge needed | Total costs/month |
|---:|---:|---:|---:|
| 50 | 2.71 bps | 36.04 bps | $40.63 |
| 250 | 2.71 bps | 9.38 bps | $203.14 |
| 1,000 | 2.71 bps | 4.38 bps | $812.57 |
| 2,000 | 2.71 bps | 3.54 bps | $1,625.13 |

At 2,000 trades a month you must gross **$2,125** to keep $500. Volume lowers
the per-trade bar and raises the toll at the same time, and the toll wins
before the bar gets low enough to be easy. This is measured, not asserted:
`test_higher_frequency_erodes_net_edge_per_trade` in the suite holds the
strategy and the data fixed, raises the trade count, and shows net edge per
trade falling.

Run it yourself:

```
python -m hft.cli feasibility
python -m hft.cli frequency
python -m hft.cli leverage
python -m hft.cli ruin
```

---

## What it costs to run

| Item | Cost |
|---|---:|
| Alpaca account, commissions, withdrawals | $0 |
| GitHub Actions (reporting) | $0 |
| SEC Section 31 fee | $20.60 per $1M sold |
| FINRA Trading Activity Fee | $0.000166/share sold, capped $8.30 |
| **Alpaca SIP market data** | **$99/month** |

The last line is the one that matters. The free data tier is IEX only, roughly
2-3% of consolidated volume. A strategy that triggers on short-horizon price
extremes computed from 3% of the tape is measuring the wrong thing. Real
scalping needs the full SIP feed, which is Alpaca's Algo Trader Plus plan at
$99/month.

That is **20% of the target, spent before the first trade**, and it is a fixed
cost that does not scale down in a bad month. Budget $1,188/year against a
$6,000/year goal. This is the one expense I need you to unblock, and I would
not spend it until the paper results justify it.

---

## What actually got built

```
hft/
  config.py        capital, target, risk limits, cost model, strategy params
  costs.py         per-fill spread, slippage, SEC Section 31, FINRA TAF
  feasibility.py   required edge, required win rate, Kelly, risk of ruin
  strategy.py      z-score mean reversion with stop, hold limit, session guard
  risk.py          hard limits + the profit governor that stops at $500
  backtest.py      event-driven, full costs, same control path as live
  marketdata.py    Alpaca bars + synthetic generator with tunable edge
  broker.py        Alpaca REST execution, double-guarded, latency probe
  runner.py        live/paper loop, persists every closed trade
  state.py         month state, committed to git
  report.py        the monthly report
  cli.py           entry point
tests/             44 tests
```

### The governor

Your "stop once $500 is hit" is implemented as a first-class control, not a
check at the end. `ProfitGovernor` holds the month's realized net P&L and
refuses new entries once the target is met; open positions still exit
normally. It also caps your exposure window, which on $3,000 is the most
effective risk control available — the account is only in the market for as
long as it takes to earn the target.

### The risk limits

On $3,000 the realistic failure is not underperformance, it is a dead account.
Checked before every entry: 3% daily loss limit, 10% monthly loss limit, 0.5%
risk per trade, 6 consecutive losses, 40 trades per day, and a hard equity
floor at 80% of capital that no new day resets.

### Order safety

`USE_ALPACA` and `HFT_LIVE_ORDERS` must **both** be exactly `true` before any
order leaves the process, including against the paper endpoint. Two tests
enforce it.

---

## Commands

| Command | What it does |
|---|---|
| `python -m hft.cli feasibility` | Required edge and win rate for the target |
| `python -m hft.cli costs` | Round-trip cost breakdown for one position |
| `python -m hft.cli backtest` | Run the strategy with full costs |
| `python -m hft.cli sweep` | How much edge the market would have to contain |
| `python -m hft.cli frequency` | Net edge per trade as volume rises |
| `python -m hft.cli leverage` | Leverage sensitivity, with and without edge |
| `python -m hft.cli ruin` | Probability of hitting the equity floor |
| `python -m hft.cli latency` | Measure broker round-trip time |
| `python -m hft.cli report` | Rebuild the monthly report |

```
pip install -r requirements.txt
python -m pytest tests -q
```

---

## Honest limits of the backtest

All external market data is blocked from the environment this was built in, so
the backtests here run on a **synthetic generator**, not real prices. That
validates the machinery — the engine loses exactly its costs on a random walk
and profits only when edge is injected — but it says nothing about whether the
edge exists in the real market.

Before any money moves, the same backtest has to run on real Alpaca minute
bars. `hft.marketdata.AlpacaBars` is written and ready; it needs your keys.
That is step one in [OPERATIONS.md](OPERATIONS.md).
