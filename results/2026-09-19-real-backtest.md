# Real-data backtest — run 2

Window 2026-06-01 to 2026-09-15. SPY and QQQ, 1-minute bars, Alpaca SIP feed.
134,618 real bars. Capital $3,000. Full cost model applied to every fill.

| Metric | Value |
|---|---:|
| Trades | 736 |
| Gross P&L | -$4.84 |
| Costs paid | $597.66 |
| **Net P&L** | **-$602.50** |
| Average net edge per trade | **-2.75 bps** |
| Win rate | 46.7% |
| Max drawdown | -$597.87 |
| Return on capital | **-20.1%** |

Halts fired, in order: 6 consecutive losses, daily loss limit, monthly loss
limit, 7 consecutive losses, and finally the equity floor at $2,397.50, which
stopped trading for good.

## What this says

**The strategy has no edge.** Gross P&L over 736 trades is -$4.84 on $3,000.
That is indistinguishable from zero. The signal is not finding anything; it is
a coin flip, and the 46.7% win rate agrees.

**The entire loss is the toll.** Costs were $597.66 and the net loss was
$602.50. Average net edge per trade was -2.75 bps against a modelled round-trip
cost of 2.71 bps. The strategy captured nothing and paid the spread 736 times.

**Volume was the mechanism of the loss, not a path to profit.** This is the
prediction in FINDINGS.md confirmed on real market data. 736 round trips at
roughly $0.81 each is $598. Trading more often would have lost more, faster,
in direct proportion. Leverage would have multiplied it.

**The risk controls worked.** Every limit fired in the designed order and the
equity floor stopped the bleed at -20% rather than letting it run to zero.
That is the system behaving correctly while the strategy behaved badly.

## Verdict

Do not fund this strategy. Nothing about position sizing, trade frequency or
leverage rescues a negative gross edge. The only thing that would change the
answer is a signal that actually predicts something, and this one does not.
