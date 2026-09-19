# Research plan: finding an edge that survives costs

## What the evidence has established

Three strategy families, 126 configurations, 20 months of real SPY and QQQ
minute bars, validated on an untouched holdout. Every finalist lost. The
decomposition against the 2.71 bps round-trip cost is the finding that matters:

| Strategy | Net bps | Implied gross bps |
|---|---:|---:|
| Opening range breakout | -13.54 | -10.83 |
| Momentum | -7.73 | -5.02 |
| VWAP reversion | -0.37 | **+2.34** |

Breakout and momentum are directionally wrong. VWAP reversion is not: it finds
a real +2.34 bps gross edge, and the cost eats all of it.

**That reframes the problem.** The task is not "find a signal." A signal was
found. The task is to find a situation where the ratio of available edge to
transaction cost is better than 2.34 : 2.71. Everything below follows from
that single ratio.

Two constraints bound the search:

- **Edge in basis points is capital-independent.** More capital multiplies
  both sides of `edge − cost` and never changes the sign. No direction here
  should be justified by "it will work with more money."
- **Cost is roughly fixed in basis points per round trip** for a given
  instrument and execution style. So the ratio improves only by finding larger
  moves, cheaper instruments, or cheaper execution.

---

## The three levers, in order of expected value

### Lever 1: larger moves per trade (raise the numerator)

Cost is paid per round trip regardless of how far price travels. A 2.71 bps
toll against a 30 bps move is 9% of the edge; against a 500 bps move it is
0.5%. Holding longer is the single cheapest way to improve the ratio, and it
requires no new infrastructure.

**Directions to test**
- **Multi-day holds.** Swing horizons of 2 to 10 days on daily bars. The same
  reversion and momentum logic, sampled where moves are an order of magnitude
  larger than the toll.
- **Overnight effects.** The close-to-open return is a well-studied and
  persistent component of equity returns, and capturing it costs one round
  trip rather than many.
- **Event windows.** Earnings, FOMC, CPI. Large moves concentrated in known
  windows, so the cost is amortised over a move the event itself generates.

**Why this is first:** it attacks the ratio directly, needs no new data feed,
and the harness already supports it. A daily-bar mode is a small change.

### Lever 2: cheaper instruments per unit of move (lower the denominator)

The toll is set by spread relative to price. What matters is spread relative
to *volatility*, because volatility is the raw material a strategy converts to
profit. An instrument with twice the spread and four times the daily range is
cheaper to trade, not more expensive.

**Directions to test**
- **Rank a universe by spread-to-ATR ratio** rather than by spread. Liquid
  index ETFs have the tightest spreads and also the smallest moves, which is
  precisely why they are hard.
- **Micro futures (MES, MNQ).** Deep liquidity, one-tick spreads, and a
  commission structure denominated in dollars per contract rather than basis
  points of notional. At small size the economics differ substantially from
  equities. Requires a different broker; Alpaca does not offer futures.
- **Higher-volatility equity ETFs**, accepting wider spreads in exchange for
  a better ratio.

**Why this is second:** genuinely promising, but futures need a new broker
integration and a new data source, so the setup cost is real.

### Lever 3: cheaper execution (lower the denominator, harder)

The cost model charges the full half-spread each way, which is what marketable
orders pay. Passive limit orders can earn part of the spread instead.

**The honest caveat:** passive fills are adversely selected. An order fills
fastest precisely when better-informed flow is trading against it, so modelled
savings routinely fail to materialise. Any work here must model fill
probability and adverse selection, not simply set `COST_SPREAD_CAPTURE` to 0.5
and celebrate. Treat this as a research question about fill modelling, not a
free improvement.

**Why this is third:** highest chance of producing a result that looks good in
backtest and fails live, which is the specific failure this harness exists to
prevent.

---

## Methodology upgrades required first

The current holdout has now informed two selection decisions. It is partially
spent. Before the next search, three changes:

### 1. Walk-forward validation replaces the single split

Rather than one fit period and one holdout, roll the window: fit on months
1-6, test on 7-8; fit on 3-8, test on 9-10; and so on. This produces many
independent out-of-sample periods instead of one, reveals whether an edge is
stable or regime-dependent, and does not exhaust a single precious holdout.

### 2. A final holdout that is never touched until the end

Reserve the most recent 4 months and do not look at it, not once, until a
single strategy has been chosen by walk-forward. That is the confirmation
test. If it fails there, the answer is no.

### 3. Correct for multiple testing

Searching K configurations inflates the best in-sample t-statistic by roughly
`sqrt(2 ln K)`. At K=126 that is about 3.1 — which is why in-sample t-values
near 1.9 meant nothing. Implement a deflated statistic so the search reports
an honest significance level rather than requiring the reader to remember K.

### 4. Pre-register each hypothesis

Write down the idea, the instruments, and the success threshold *before*
running it, committed to the repository. This is what separates research from
narrative fitting. The run-1 episode is instructive: breakouts failing
suggested reversion should win, reversion was then tested and came in flat.
Had that hypothesis been formed after seeing the holdout and tested on the
same holdout, a flat result could easily have been dressed up as a win.

---

## Decision gates

A strategy advances only by clearing each gate in order. No skipping.

| Gate | Requirement |
|---|---|
| 1. Gross edge | Gross edge per trade exceeds 2× the modelled round-trip cost |
| 2. Walk-forward | Positive net edge in at least 70% of rolling out-of-sample windows |
| 3. Deflated significance | t-statistic clears the multiple-testing-adjusted threshold |
| 4. Final holdout | Positive net edge, t > 2, on the never-touched window |
| 5. Paper month | Positive net edge per trade live, consistent with backtest |
| 6. Funded | Start at 1x leverage, smallest viable size |

Gate 1 is deliberately strict. A strategy that only just clears costs in
backtest will not clear them live, because real slippage exceeds modelled
slippage and the edge decays as others find it.

## Stop conditions

Research should stop, and the answer accepted as no, if any of these hold:

- Three more families across the levers above fail at gate 1 or 2.
- The best surviving candidate's gross edge is under 2× cost after walk-forward.
- Total research time exceeds what the realistic payoff justifies. At a
  plausible 1-3% monthly return on $3,000, that is $30-90/month. Spending
  another hundred hours to chase it is a losing trade in the only currency
  that actually matters.

Stopping is a legitimate outcome. The harness has already paid for itself by
preventing a funded strategy that would have lost 20% of capital in three
months.

---

## Realistic targets

For calibration, on $3,000:

| Outcome | Monthly | Assessment |
|---|---:|---|
| Treasury bills, no risk | $9 | The honest baseline |
| Good retail intraday strategy | $30-90 | 1-3% monthly, would beat most funds |
| The original target | $500 | 16.7% monthly, 536% annualised |

A genuine 1-3% monthly return is an excellent outcome and worth pursuing. The
gap between that and $500 is not a gap in effort or cleverness; it is the gap
between a $3,000 account and a $165,000 one.

---

## Immediate next steps

1. Implement walk-forward validation and the deflated statistic in
   `hft/search.py`. Independent of any strategy, and improves every future run.
2. Add daily-bar support so multi-day horizons can be tested. The largest
   ratio improvement for the least new infrastructure.
3. Pre-register and test lever 1: overnight and multi-day reversion on a
   universe wider than SPY and QQQ, on data before the reserved final holdout.
4. Report against the gates above, and stop if gate 1 fails.
