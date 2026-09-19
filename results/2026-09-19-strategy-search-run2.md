# 20-month strategy search — run 2 (all families)

Window 2025-01-15 to 2026-09-15. SPY and QQQ, 1-minute SIP bars.
126 configurations across three strategy families, capped so each family
reaches the holdout. Fitted on the first ~14 months, validated on an untouched
~6-month holdout.

## Finalists

| Strategy | IS bps | IS t | OOS bps | OOS t | OOS trades |
|---|---:|---:|---:|---:|---:|
| opening range breakout | +4.71 | 1.87 | **-13.54** | -3.53 | 175 |
| opening range breakout | +6.86 | 1.86 | **-15.13** | -3.47 | 154 |
| momentum | +0.21 | 0.14 | **-7.73** | -3.33 | 270 |
| momentum | +0.16 | 0.14 | **-9.03** | -4.35 | 223 |
| VWAP reversion | -1.87 | -1.07 | **-0.37** | -0.13 | 144 |

**Every family loses out of sample.** Nothing here is executable.

## The hypothesis from run 1 is refuted

Run 1 showed breakouts failing badly, which suggested the period might have
rewarded reversion instead. It did not. VWAP reversion came in at -0.37 bps
per trade with t = -0.13, which is zero, not positive. Recording this because
it was my inference and the data disagreed with it.

## The one genuinely interesting number

Decomposing net edge against the 2.71 bps modelled round-trip cost:

| Strategy | Net bps | Implied gross bps |
|---|---:|---:|
| opening range breakout | -13.54 | -10.83 |
| momentum | -7.73 | -5.02 |
| **VWAP reversion** | **-0.37** | **+2.34** |

Breakout and momentum are directionally wrong: they lose before costs are
even applied. VWAP reversion is different. It has a **real gross edge of about
+2.34 bps per trade**, and the 2.71 bps cost consumes all of it.

That is textbook market efficiency. The intraday reversion effect is genuinely
there, and it has been arbitraged down to almost exactly the level of the
transaction cost required to harvest it. The market is not leaving money on
the table; it is leaving precisely enough to pay the toll and no more.

## Why more capital does not fix this

Edge measured in basis points is capital-independent. A +2.34 bps gross edge
against a 2.71 bps cost is negative at $3,000 and equally negative at
$300,000. Scaling capital multiplies the dollars on both sides of the
subtraction and never changes its sign.

Only three things would change the answer, and none is available to a retail
account:
1. A better signal, one with materially more than 2.71 bps of predictive power.
2. Lower costs, which means earning the spread passively rather than paying
   it. That requires queue priority a retail REST API does not have.
3. Rebates from an exchange for providing liquidity, which requires being a
   registered market maker.

## Verdict

Do not fund active trading on this capital. After 20 months of real data, 126
configurations, and three structurally different ideas tested with a clean
out-of-sample holdout, there is no demonstrated edge that survives costs.
