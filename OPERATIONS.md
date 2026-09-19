# Operations runbook

## Before any money moves

The backtests in this repository ran on synthetic data, because market data is
blocked from the build environment. They prove the engine is correct. They
prove nothing about profitability. Do these in order.

**Step 1 — real-data backtest.** Add Alpaca keys as repository secrets, then
run the **real-data-backtest** workflow from the Actions tab with a start and
end date. Read `FINDINGS.md` first so you know which number decides it:
average net basis points per trade, after costs. Free.

This runs on a GitHub runner rather than in a Claude cloud session, because
that environment's network policy blocks alpaca.markets. The workflow prints
a verdict alongside the raw numbers.

**Step 2 — paper trade for one month.** `USE_ALPACA=true`, base URL set to the
paper endpoint, `HFT_LIVE_ORDERS=true`. No real money at risk. This requires
the $99/month SIP data subscription to be meaningful, because the free IEX
feed is 2-3% of volume and will give the strategy a distorted picture.

**Step 3 — decide.** If average net edge per trade is not clearly positive
across several hundred paper trades, stop. If it is, fund with an amount you
are willing to lose entirely and start at 1x leverage.

## Division of labor

| Step | Who |
|---|---|
| Open and fund the Alpaca account | You, once |
| Subscribe to SIP data ($99/mo) | You, when step 2 begins |
| Add API keys as GitHub secrets | You, once |
| Real-data backtest and paper month | Me |
| Tune, monitor, run the strategy | Me |
| Monthly report | Me |
| Withdraw the cash | You, monthly |

Alpaca's Trading API does not expose transfers for individual accounts;
programmatic withdrawals live in the Broker API, which is for licensed firms.
So the monthly withdrawal is one click in the dashboard. ACH is free.

## Setup

1. Open an Alpaca individual account. No minimum, commission-free.
2. Generate API keys. The secret is shown once.
3. Repository Settings → Secrets and variables → Actions:
   - Secrets: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`
   - Variables: `ALPACA_BASE_URL`, `USE_ALPACA`, `HFT_LIVE_ORDERS`,
     `HFT_CAPITAL`, `HFT_MONTHLY_TARGET`, `ALPACA_DATA_FEED`
4. Keep `HFT_LIVE_ORDERS=false` until step 3 above is passed.

## Monthly rhythm

The strategy runs during market hours and halts itself when the month's $500
is realized. On the 1st, a scheduled job rebuilds the report and commits it.
You read one page and withdraw.

The report states: realized net profit, whether the target was hit and when,
account equity, trade count, win rate, gross P&L, costs paid, and average net
edge per trade. Watch that last number over time. It is the health of the
strategy; the monthly total is just its consequence.

## Halt conditions

| Trigger | Effect |
|---|---|
| $500 realized | Stops for the month. Resumes on the 1st. |
| 3% daily loss | Stops for the day. Resumes next session. |
| 10% monthly loss | Stops for the month. |
| 6 consecutive losses | Stops until a new day. |
| Equity below 80% of capital | Full halt. Does not reset. Requires your decision. |

The equity floor is deliberate. If the account is down 20%, the strategy has
been wrong for long enough that it should not be allowed to keep going without
a human looking at it.

## Security

The original `.env.example` in commit `481f9b7` contained real-looking Alpaca
paper API keys, and they remain in git history. `.env` is now gitignored and
the example file holds only placeholders, but history was not rewritten.
**Revoke those keys in the Alpaca dashboard.** Live keys belong in GitHub
Secrets, never in a file.
