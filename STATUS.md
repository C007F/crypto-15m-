# crypto-15m-bot — status handoff

**Written 2026-08-24 ~19:10 UTC, on Chester.** Everything below lives at
`/home/cam/projects/crypto-15m-bot`. Nothing is anywhere else. Repo has **no
git remote** by design.

---

## TL;DR

Six live traders are running micro on **$200 of real Kalshi money**, one
contract max. **Balance unchanged at $199.9830. Zero positions. 9 orders
attempted, 0 filled.**

The open question is whether micro's paper edge survives a retail order path.
Current evidence says **probably not**, but the sample is small.

---

## The strategy

`micro` (microstructure expert) buys when Kalshi's book lags Coinbase spot.

Measured over **277 paper trades across six coins**:

| | |
|---|---|
| hit rate | 25.6% |
| breakeven hit rate | 7.9% |
| avg win / avg loss | $4.23 / $0.36 (**11.7 : 1**) |
| expectancy | **+$0.82 / trade** |
| net | +$226 |
| P(luck) | 5 x 10^-19 |

Positive on **all six** coins. A chi-square across assets gives p=0.22, so the
coins are statistically **indistinguishable** — treat this as one strategy on
six instances, not six edges. Do not concentrate into BTC because it looks
best; that is reading noise.

**The entire edge is the cut.** Wins are settlements (40c -> $1). Losses are
early exits costing ~5c (spread + both fees). If cuts stop working, expectancy
flips from **+$0.11 to -$0.16 per contract**. Everything else is secondary.

---

## Live results so far

**9 orders. 0 fills. Every one rejected with
`fill_or_kill_insufficient_resting_volume`.**

The quote was gone before the order landed.

### The measurement that matters

```
Chester            Clifton NJ (same metro as the exchanges)
Kalshi round trip  26ms median / 33ms p90
poll interval      2000ms
book stillness     86% of 2s intervals move 0 ticks
                   14% move >1 tick, 3% move >3 ticks
```

Micro fires **precisely when the book is not still** — a stale quote is stale
because the market just moved. **The signal and the fill failure share a
cause.** Our orders systematically land in the fast 14%.

### The one data point with real timing

```
19:05:54  btc  no  @0.61  filled=0  book_age=54ms  round_trip=30ms
```

**A 54ms-stale book was still rejected.** If that pattern holds, a WebSocket
feed will NOT fix this and the edge is not reachable at retail speed. One
sample — do not conclude from it yet. Wait for ~20 orders with timing.

### Decision rule for the WebSocket build

```
rejections cluster at HIGH book_age  ->  build the WS feed, it will help
rejections spread evenly / low age   ->  do not build it, edge unreachable
```

Endpoint confirmed to exist: `wss://api.elections.kalshi.com/trade-api/ws/v2`
(returns 401 not 404). Do not build it before the data says to.

Run `FillLog.latency_report()` for the bucketed table.

---

## Safety (all verified live, 19/19 pre-flight)

Run `.venv/bin/python preflight.py` before arming anything.

- **Only micro trades real money.** `LIVE_BOOKS = {"micro"}`. momentum
  (-$0.18/trade) and lotto (-$0.19/trade) are paper-only.
- **$15/day loss cap**, account-wide, shared across all six processes with an
  exclusive flock. Was per-coin — that was $90/day of real risk.
- **$50 equity floor.** Unknown balance **halts** (fails closed).
- **Staged sizing** 1 -> 3 -> 5 -> 8 contracts, 50 orders per stage. Survives
  restarts; the daily counter resets overnight, the stage does not.
- **Kill switches**, checked every tick: `data/LIVE_KILL` (all coins),
  `data/<coin>/LIVE_KILL` (one coin).
- **Cannot move money.** Endpoint allowlist raises on any path containing
  deposit/withdraw/transfer/payment/bank/ach/wallet. $200 is a hard ceiling.
- **Dashboards behind basic auth**, `cam` / see `/root/.dashboard_password`.
- Six tests fail the build if any secret can reach a web response.

---

## Bugs found today (all fixed) — read this before trusting anything

Ten. Five would have lost money silently.

1. **`flatten()` never reached the exchange.** Only edited the local ledger.
   Live, the bot books a 5c cut while the real contract rides to settlement
   for the full 40c. **This alone inverts the strategy.**
2. **HTTP 200 was treated as filled.** fill_or_kill returns a successful
   response with `fill_count: 0`. The bot would have booked positions it did
   not own and fed imaginary losses into the daily cap.
3. **The daily loss cap was dead code.** `record_close()` was defined and
   never called. Now wired at `_review()`, the single choke point.
4. **The cap was per-coin.** Six coins x $15 = $90/day of real risk while the
   operator believed it was $15.
5. **Failed balance fetch fell back to $1000**, disabling the equity floor on
   a $200 account. Now fails closed.
6. **Wrong order endpoint.** `/portfolio/orders` is V1 and returns HTTP 410.
   V2 is `/portfolio/events/orders`. (I broke this "fixing" working code.)
7. **Response parser read every fill as zero.** V2 returns `fill_count` at the
   top level, no `order` wrapper. Looks like a dead strategy, is a typo.
8. **Exits priced at the last-seen quote were rejected.** Now cross hard
   (0.01 to sell, 0.99 to buy). fill_or_kill executes at the BEST AVAILABLE
   price, not the limit — a 0.22 entry filled at 0.86 — so crossing is free.
9. **Live inherited paper's lessons**, raising micro's bar from 0.08 to 0.14.
   A 75% higher threshold is a different strategy. Live now writes
   `lessons_live.jsonl`.
10. **The bot logged "bought" when nothing filled.** Money was safe; the log
    lied. `_fill()` now returns the filled count.

**Base rate warning:** every one of these was in code I had already written
and reviewed. Assume more remain.

---

## Operations

```bash
cd /home/cam/projects/crypto-15m-bot

# status
systemctl --user list-units 'crypto15m-live@*'
tail -f data/btc/live.log

# stop everything, within one tick
touch data/LIVE_KILL

# stop one coin
touch data/btc/LIVE_KILL

# hard stop
systemctl --user stop 'crypto15m-live@*'

# checks before arming
.venv/bin/python preflight.py
.venv/bin/python -m unittest discover -s tests

# one real 1-contract order, end to end (<$1 risk)
.venv/bin/python -m crypto15m livecheck
```

Live units are **installed but not enabled** — no autostart on reboot.

**Paper traders are stopped.** Twelve processes polling Kalshi rate-limits,
and a 429 arriving when a cut needs placing is the failure that turns a 5c
loss into 40c. Paper history is preserved in `data/<coin>/`.

Dashboard: `http://45.55.79.111/db589b7806aff1205013854472d697e5/crypto/`

---

## What to do next

1. **Wait for ~20 orders with timing data.** Then read
   `latency_report()`. That decides the WebSocket question.
2. **If fills stay at zero**, the honest conclusion is that the edge exists in
   the data but is not reachable through a retail order path. That is a real
   answer and the $200 bought it cheaply.
3. **If fills start landing**, watch two numbers: entry fill rate, and how
   often a *cut* fails. A failing cut is the one that inverts everything.
4. Do not raise size past stage 1 until fill rate is understood.
5. Rotate the Kalshi API key — it was pasted into a chat transcript.

---

## Things that are NOT settled

- Whether micro's edge survives real fills. **This is the whole open question.**
- Whether ZEC's 26:1 payoff is real or an artifact of its 5c spread. Its cuts
  cost 9c against everyone else's 5c.
- SOL has only ~20 contracts on the bid. At stage 4 (8 contracts) you would be
  taking 40% of the visible book.
- The tuner has never run against live data.
