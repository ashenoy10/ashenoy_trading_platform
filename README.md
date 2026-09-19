# Income Platform — $500/month, held flat

A single-purpose system. It does not try to beat the market. It holds a
Treasury-bill ETF, collects the monthly distribution, pays out **exactly
$500**, keeps a reserve that absorbs the months when income lands short, and
reinvests every dollar of surplus. Your only jobs are to fund it once and to
withdraw the cash each month.

Target: **$500.00/month, ±$50 tolerance.** The engine pays exactly $500 and
treats anything above it as surplus, so the band is only ever used for
reporting. See [OPERATIONS.md](OPERATIONS.md) for the runbook.

---

## How much money you need

Income is principal × yield. There is no way around that arithmetic, so the
capital number is set by the yield of whatever you are willing to hold.

The instrument this platform defaults to is **SGOV** (iShares 0-3 Month
Treasury Bond ETF), whose 30-day SEC yield was **3.65%** on 17 September 2026,
net of its 0.09% expense ratio.

| | Amount |
|---|---:|
| Break-even principal at 3.65% | $164,384 |
| Principal with 5% cushion (what the platform sizes) | **$172,603** |
| Starter cash reserve (6 × $500) | **$3,000** |
| **Total to deposit** | **$175,603** |

The cushion makes the portfolio earn about $525/month in the base case. That
$25 surplus is reinvested, which grows principal and keeps the payout safe as
rates drift down.

Run the numbers yourself at any time:

```
python -m income.cli size
python -m income.cli plan      # includes yield sensitivity
```

### If $175k is more than you want to commit

Lower capital is available only by accepting risk to the principal. These are
the honest options, at current yields:

| Approach | Yield | Principal for $500/mo | What you are accepting |
|---|---:|---:|---|
| T-bill ETF (SGOV) — **default** | 3.65% | $164,000 | Essentially none. Backed by Treasuries, state-tax exempt. |
| High-yield savings | up to 4.21% | $143,000 | FDIC insured, but the rate is variable and this platform cannot automate a bank. |
| 70% SGOV / 30% JEPI | ~5.0% | $120,000 | ~30% equity exposure. A 30% equity drawdown costs roughly 9% of the portfolio. |
| Covered-call ETF (JEPI) | ~8.15% | $74,000 | Distributions vary month to month, and NAV erodes in flat-to-down markets. The $500 is no longer reliable. |

My recommendation is the default. You asked for $500 a month with no surprises,
and the only configuration that actually delivers that is the boring one. If you
want the $120,000 blend instead, say so and I will change the instrument mix and
re-run the sizing; the engine handles it without code changes.

---

## What it costs to stand up

Nothing. Every service in the stack has a free tier that covers this workload.

| Item | Cost | Note |
|---|---:|---|
| Alpaca brokerage account | $0 | No minimum, no inactivity fee, commission-free US equities/ETFs. |
| Alpaca market data | $0 | The free tier is enough. This platform reads account and activity endpoints, not real-time quotes. The $99/mo Algo Trader Plus plan is **not** needed. |
| GitHub Actions (the scheduler) | $0 | ~2 minutes of compute per month against a 2,000 min/month free allowance on private repos, unlimited on public. |
| Server / VPS | $0 | There isn't one. The monthly job runs on GitHub's runner. |
| ACH withdrawal | $0 | Alpaca does not charge for withdrawals. |
| SEC Section 31 fee | ~$0.01/yr | $20.60 per $1M, sells only. Only charged in the rare month we sell principal. |
| FINRA trading activity fee | <$0.01 | Per-share, sells only, capped at $8.30 per trade. |
| SGOV expense ratio | $155/yr | 0.09% on $172,603. Already deducted from the 3.65% yield above, not a separate bill. |

**There is nothing for you to unblock or pay for.** The only money that moves is
your capital.

One thing that is not a fee but is real: **taxes.** The $6,000/year is ordinary
income, taxable federally, and nothing is withheld. Treasury interest is exempt
from state and local tax, which is a meaningful edge in a high-tax state. Set
aside your marginal rate on the $6,000, or tell me and I will size the principal
so that $500 is the *after-tax* number.

---

## How it works

Once a month the scheduled job runs one cycle:

1. Read the SGOV distributions that landed since the last run.
2. Add them to the reserve. That pool is what the payout comes from.
3. Pay exactly $500. If the pool is short, the reserve covers it; only if the
   reserve is empty does the platform sell principal, and it flags that loudly.
4. Refill the reserve up to six months of target.
5. Reinvest whatever is left into SGOV.
6. Write the report and commit the ledger to git.

Cash sitting in the account after a cycle is your payout plus the reserve. The
report tells you exactly which number is yours to take.

The reserve is the part that makes the payout constant. Distributions are not
flat month to month, and yields move. Simulated over 18 months with a yield
collapse from 3.65% to 2.00% in month 6, the payout is $500.00 every single
month, with the reserve absorbing the entire gap:

```
python -m income.cli simulate --months 18 --shock-month 6 --shock-yield 0.02
```

When income runs persistently below target, the report says so and tells you
whether to add capital or accept a slow principal drawdown. It will not quietly
eat your principal.

---

## Commands

| Command | What it does |
|---|---|
| `python -m income.cli size` | Capital required for the target |
| `python -m income.cli plan` | Forecast plus yield sensitivity table |
| `python -m income.cli status` | Account and ledger snapshot, including withdrawable cash |
| `python -m income.cli deploy` | One-time: invest the deposit, hold back the reserve |
| `python -m income.cli run` | The monthly cycle; writes `reports/monthly/YYYY-MM.md` |
| `python -m income.cli simulate` | Multi-month dry run, no state touched |

Everything defaults to a simulated account. Real orders require **both**
`USE_ALPACA=true` and `INCOME_LIVE_ORDERS=true`; either one missing and the
platform refuses to place an order, even against the paper endpoint. That is
covered by a test.

```
pip install -r requirements.txt
python -m pytest tests -q
```

---

## Layout

```
income/
  config.py     target, tolerance, instrument, yields — all env-overridable
  sizing.py     capital math: principal, reserve, months of cover
  accounts.py   SimulatedAccount + AlpacaAccount (REST, order-guarded)
  ledger.py     append-only JSON record of every cycle, committed to git
  engine.py     the monthly cycle and the payout rules
  report.py     markdown + json monthly report
  cli.py        entry point
tests/          19 tests covering sizing, payout invariants, and safety
.github/workflows/
  monthly-cycle.yml   scheduled run, commits ledger and report
  tests.yml           CI
```
