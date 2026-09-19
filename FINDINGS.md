# Findings: can $3,000 scalp $500 a month?

Everything here is reproducible from the repository. Commands are listed under
each section.

## 1. The target implies a 536% annual return

$500/month on $3,000 is 16.67% per month. Compounded, 535.9% a year. For
scale, Renaissance Technologies' Medallion fund is the most successful
quantitative fund on record and returned roughly 66% a year gross. The target
asks for about eight times that, on retail infrastructure.

This alone does not make it impossible. It does mean the burden of proof sits
with the strategy, and the rest of this document is about whether volume can
carry that burden.

## 2. Volume makes the toll bigger, not the problem smaller

`python -m hft.cli feasibility`

Costs are charged per round trip: half the quoted spread each way, slippage,
plus SEC Section 31 ($20.60/$1M, sells only) and the FINRA Trading Activity
Fee. On $3,000 of SPY at a 1.5 bps spread, one round trip costs **2.71 bps**,
about $0.81.

| Trades/month | Gross edge needed per trade | Total cost/month | Gross needed/month |
|---:|---:|---:|---:|
| 20 | 86.04 bps | $16.25 | $516 |
| 100 | 19.38 bps | $81.26 | $581 |
| 250 | 9.38 bps | $203.14 | $703 |
| 420 | 6.68 bps | $341.28 | $841 |
| 1,000 | 4.38 bps | $812.57 | $1,313 |
| 2,000 | 3.54 bps | $1,625.13 | $2,125 |

Volume lowers the edge required per trade. It raises the total that must be
grossed by exactly as much as it lowers the per-trade bar. At 2,000 trades
that is a $2,125/month operation to keep $500.

The common premise is that volume converts a small per-trade edge into a large
income. The table shows the opposite: volume converts a small per-trade edge
into a large cost base that the edge then has to cover.

## 3. The strategy confirms it

`python -m hft.cli frequency`

Holding the data and the strategy fixed and only loosening the entry threshold
to trade more often, on synthetic data with strong mean reversion injected:

| Entry z | Lookback | Trades/month | Net P&L | Net bps/trade |
|---:|---:|---:|---:|---:|
| 0.75 | 5 | 728 | $42.60 | 0.20 |
| 0.75 | 20 | 588 | $297.04 | 1.68 |
| 1.00 | 20 | 475 | $363.74 | 2.55 |
| 1.50 | 20 | 262 | $301.07 | 3.83 |
| 2.00 | 20 | 117 | $170.07 | 4.85 |

728 trades earns $42.60. 475 trades earns $363.74. Net edge per trade falls
faster than trade count rises, so profit peaks in the middle and collapses at
high frequency. `test_higher_frequency_erodes_net_edge_per_trade` pins this
behaviour.

## 4. On a random walk it loses exactly its costs

`python -m hft.cli backtest --reversion 0`

With no edge in the data by construction: 102 trades, gross -$32.47, costs
$81.50, **net -$113.97**, and the run halts on six consecutive losses. This is
the null result the engine is supposed to produce, and it is asserted in
`test_random_walk_loses_approximately_its_costs`.

It is also what a strategy with no real edge does to an account: bleeds it at
the rate of the spread.

## 5. Leverage is the only lever that reaches the target

`python -m hft.cli leverage`

With generous mean reversion injected and tuned parameters:

| Leverage | Position cap | Net/month with edge | Net/month with NO edge |
|---:|---:|---:|---:|
| 1x | $3,000 | $363.74 | -$25.25 |
| 2x | $6,000 | $501.19 | -$50.50 |
| 4x | $12,000 | $508.55 | -$101.00 |

2x leverage clears $500. It also doubles the loss when the edge is absent,
and 4x quadruples it. Leverage multiplies whatever edge is present, including
a negative one. The FINRA pattern day trader rule and its $25,000
minimum were eliminated effective 4 June 2026, so leverage is available to a
$3,000 account in a way it was not before. That change makes this easier to
attempt and no easier to survive.

## 6. Risk of losing the account

`python -m hft.cli ruin`

Monte Carlo, 20,000 paths, $15 risked per trade, 420 trades, ruin defined as
equity touching $2,400:

| Win rate | P(hit floor) | Kelly | Expectancy/trade |
|---:|---:|---:|---:|
| 40% | 99.2% | -0.20 | -$3.00 |
| 45% | 63.6% | -0.10 | -$1.50 |
| 50% | 5.3% | 0.00 | $0.00 |
| 55% | 0.0% | 0.10 | +$1.50 |

The cliff between 45% and 55% is the whole game. Everything depends on which
side of break-even the strategy actually lands, and nothing in a backtest of
synthetic data can establish that.

## 7. The infrastructure this competes against

The per-trade edge this requires, 4 to 7 bps, is in the range that
market-making firms capture — with colocated servers, direct exchange feeds,
and microsecond latency. A retail REST API is three to four orders of
magnitude slower. `python -m hft.cli latency` measures yours once keys exist;
expect tens to hundreds of milliseconds.

At that latency the spread is paid, not captured. That is why the cost model
charges the full half-spread on both sides by default. To model passive
execution instead, set `COST_SPREAD_CAPTURE` below 1.0 and re-run, bearing in
mind that passive orders do not always fill and the ones that fill fastest are
the ones filled against by better-informed flow.

## 8. Tested on real data: the strategy loses, exactly as predicted

`results/2026-09-19-real-backtest.md` has the full run. 134,618 real SPY and
QQQ minute bars from Alpaca's SIP feed, 1 June to 15 September 2026.

| Metric | Value |
|---|---:|
| Trades | 736 |
| Gross P&L | -$4.84 |
| Costs paid | $597.66 |
| Net P&L | -$602.50 |
| Average net edge per trade | -2.75 bps |
| Return on capital | -20.1% |

Gross P&L across 736 trades is -$4.84, which is zero within noise. The signal
predicts nothing. The whole loss is the cost base: $597.66 of spread, slippage
and fees, against a modelled round-trip cost of 2.71 bps and a realized net
edge of -2.75 bps per trade.

This is section 2 confirmed on real prices. Volume did not convert a small
edge into $500; it converted no edge into a 20% drawdown in three months. The
equity floor halted trading at $2,397.50, which is the risk system working.

---

## What the evidence supports

1. **Measure before funding.** A real-data backtest on historical SIP bars is
   free and is the only thing that turns "does an edge exist" from an opinion
   into a measurement. If net edge per trade is not clearly positive after
   costs, no amount of volume or leverage fixes it.
2. **Judge on edge per trade, not total profit.** Eighty dollars across 400
   trades at a positive per-trade edge is a far better sign than three hundred
   across twelve lucky ones.
3. **Match the target to the capital.** A monthly income target implies a
   principal, and at Treasury yields $500/month implies roughly $165,000.
   Small accounts are research budgets, not income bases.

[RESEARCH_PLAN.md](RESEARCH_PLAN.md) sets out where the search goes next and
the conditions under which it should stop.
