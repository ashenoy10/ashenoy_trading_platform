# Operations runbook

## Division of labor

I run the platform. Three things are legally or technically yours, and no
amount of engineering removes them.

| Step | Who | Why |
|---|---|---|
| Open the Alpaca account | You, once | Brokerage account opening requires identity verification against your SSN. It cannot be delegated. |
| Fund it ($175,603) | You, once | Linking your bank and moving money requires your authorization. |
| Add API keys as GitHub secrets | You, once | ~5 minutes. Steps below. |
| Deploy the capital into SGOV | Me | `income.cli deploy` |
| Run the monthly cycle | Me | Scheduled, 2nd of each month |
| Produce the monthly report | Me | Committed to `reports/monthly/` |
| Withdraw the $500 | **You, monthly, one click** | See the constraint below. |

### The withdrawal constraint

Alpaca's Trading API, which is what an individual account gets, exposes orders
and positions but **not** transfers. Programmatic ACH withdrawals live in the
Broker API, which is for licensed firms onboarding their own customers, not for
your own account. Alpaca's own documentation states that users cannot
programmatically schedule deposits or withdrawals.

So the monthly cash does not move itself. What the platform does instead is
make the amount unambiguous: after each cycle the report states one number,
and the cash is sitting in the account waiting. You log in, withdraw that
amount, done. ACH is free and takes a few business days.

If you want this fully hands-off, the alternative is to let the cash accumulate
and withdraw quarterly or annually. The platform tracks un-withdrawn cash
separately from the reserve, so it will tell you the running total. Say the
word and I will switch the report to a quarterly cadence.

## One-time setup

1. **Open an Alpaca individual brokerage account** at alpaca.markets. Free, no
   minimum. Link your bank during onboarding.
2. **Fund it with $175,603.** Confirm the cash has settled before step 5.
3. **Generate live API keys** from the Alpaca dashboard. Copy both halves; the
   secret is shown once.
4. **Add them to this repository**, Settings → Secrets and variables → Actions:
   - Secrets: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`
   - Variables: `ALPACA_BASE_URL` = `https://api.alpaca.markets`,
     `USE_ALPACA` = `true`, `INCOME_LIVE_ORDERS` = `true`,
     `INCOME_TARGET_MONTHLY` = `500`, `INCOME_INSTRUMENT` = `SGOV`,
     `INCOME_PLANNING_YIELD` = `0.0365`
5. **Tell me it's funded.** I run `deploy`, which buys SGOV with everything
   except the $3,000 reserve, and I confirm the position.

Before step 4, I strongly suggest one dry run against the paper endpoint
(`ALPACA_BASE_URL=https://paper-api.alpaca.markets`) to watch a full cycle
execute. It costs nothing and takes a day.

## Monthly rhythm

- **2nd of the month, 14:00 UTC** — the scheduled job runs. It reads the
  month's distributions, pays out, rebalances the reserve, reinvests the
  surplus, writes `reports/monthly/YYYY-MM.md`, and commits the ledger.
- **You** read the report and withdraw the stated amount.

The report always contains: the payout available, total un-withdrawn cash,
income received, reserve movement, amount reinvested, current principal, and
next month's forecast. If anything needs your attention, it appears under an
**Action required** heading. If that heading is absent, there is nothing to do
but withdraw.

## Failure modes and what happens

| Situation | What the platform does |
|---|---|
| Distribution smaller than $500 | Reserve covers the gap. Payout stays $500. |
| Yields fall hard and stay down | Reserve drains over several months. Once it drops under two months of target, the report raises **Action required** and tells you the choice: add capital or accept slow principal drawdown. |
| Reserve empty and income still short | Sells exactly enough SGOV to pay $500, and logs the sale. Payout still $500. |
| Principal exhausted | Pays whatever is left and flags `PRINCIPAL EXHAUSTED`. Cannot happen for decades at these yields. |
| Job fails to run | The cycle is idempotent per period. Re-running is safe; a duplicate is refused. |
| Job runs twice | Second run refuses with "already processed". |
| Credentials missing or wrong flags | Orders raise `PermissionError` before anything is sent. |

## Safety properties, each covered by a test

- The payout is exactly the target. A windfall does not raise it; a shortfall
  does not lower it until principal is genuinely gone.
- Principal is never sold while the reserve has money in it.
- No order is ever placed unless `USE_ALPACA` and `INCOME_LIVE_ORDERS` are both
  literally `true`.
- The ledger is append-only and lives in git, so every cycle is auditable and
  reconstructable.
- A period can only be processed once.

## Security note — act on this

The repository's original `.env.example` contained what appear to be **real
Alpaca paper-trading API keys**, and they are in the git history of commit
`481f9b7`. I have removed them from the working tree and added `.env` to
`.gitignore`, but history rewriting is destructive so I have not touched it.

Revoke those keys in the Alpaca dashboard. They are paper keys, so the exposure
is limited to a simulated account, but rotate them anyway and never reuse that
pattern for live keys. Live keys go in GitHub Secrets, never in a file.
