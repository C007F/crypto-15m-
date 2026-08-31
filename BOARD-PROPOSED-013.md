# Board Memo 013 — 39 consecutive misses, and what the board will not conclude
*Proposed 2026-08-31 ~15:00 UTC. Diagnostic. One decision requested.*

Last successful fill **13:12:08 UTC**. Thirty-nine attempts since, zero fills.
The chairman's read that "something is up" is correct. The board is asked to
rule on how to find out, not on what it is — because the seat that would tell
you what it is has been wrong three times today.

---

## Ruled out, verified live against the exchange

| | |
|---|---|
| stream | 0 restarts since 8/28; book files 0–1s old; its books match Kalshi REST |
| spot feed | fresh AND accurate — BTC 78400.24 vs Coinbase 78432.51 (~4bps) |
| liquidity | BTC showed **6,017 contracts** resting. Not thin. |
| order body | well-formed; `exchange_index: 2` matches the market; IOC, so partials are allowed |
| self-trade prevention | no resting orders on the account |
| phantom fills | Kalshi reports `portfolio_value: 0`, zero open — they genuinely are not filling |
| process health | no restarts, nothing halted, balance entirely on exchange 2 |
| **the order path itself** | **unchanged in git since 8/28.** `client.py`, `decision.py`, `edge.py`, `ticks.py` all untouched. This is not a code regression. |

## The anomaly, measured properly

`SLIPPAGE_TICKS=3` × `CROSS_CUSHION=0.05` prices every buy **5 cents through
the ask**, by design, and the EV is computed at that price. Joined to the bot's
own tape at the **exact same tick** (no tolerance):

```
sol    limit 0.31   ask 0.26   rem 222s   -> 0
hype   limit 0.49   ask 0.44   rem  81s   -> 0
doge   limit 0.38   ask 0.33   rem 136s   -> 0
eth    limit 0.37   ask 0.32   rem  80s   -> 0
```

21 such orders. All zero. Kalshi returns `filled: 0, remaining_count: 0`.

## The context the seat missed this morning

YES has **always** filled worse than NO. This is not new today:

| day | YES | | NO | |
|---|---|---|---|---|
| 8/26 | 26/29 | 90% | 34/38 | 89% |
| 8/27 | 6/13 | 46% | 61/62 | 98% |
| 8/28 | 8/29 | 28% | 61/70 | 87% |
| 8/29 | 5/6 | 83% | 57/62 | 92% |
| 8/30 | 7/10 | 70% | 88/105 | 84% |
| **8/31** | **3/45** | **7%** | **23/61** | **38%** |

Two things happened at once. **The signal flipped side** — YES attempts went
from 6–10 a day to **45** — and YES is the side that has always filled badly.
Part of today's collapse is composition, not breakage. But 7% against a prior
range of 28–90%, with NO also halving to 38%, is more than composition.

A structural clue, not yet a conclusion: on a live quote the board pulled,
`yes_ask_size` was **103** against `no_ask_size` **7,595**. There is far less
size available to buy YES than to buy NO in these books.

---

## Seats

**Musk seat — FOR livecheck, now, and stop theorising.** You have spent a
morning inferring the contents of an HTTP response that you throw away. One
contract, under a dollar, end to end, and you will know in thirty seconds
whether a 5c-through YES bid fills. That is not a risk, it is the cheapest
experiment on the board. Do the logging too, but do not wait for it — logging
tells you about the *next* failure, livecheck tells you about *this* one. And
do not halt: an unfilled order costs nothing, and halting deletes the only
evidence generating itself right now.

**Buffett seat — FOR both, AGAINST halting, and note what is actually at
stake.** Nobody is losing money here. Misses are free, the account is $277.14
and up $8.73 today. What is at stake is that the book has quietly become a
NO-only strategy — 23 of 26 fills today were NO — and no one voted for that.
The coach is also learning from a sample that excludes every YES trade the bot
wanted and could not get, which is how a measurement problem turns into a
strategy drift. Instrument it. But I want the composition point in the record:
this may be substantially "the signal moved to the side that never filled well,"
and that is a very different memo from "the order path broke."

**Claude seat — FOR both. I own three failures today and I will not offer a
fourth theory.** I told the chairman `conviction_size()` was blind to the book
(it is not — `pick_side` caps at resting size first). I reported an uncertainty
finding that reversed sign under split-half. I produced a "limit below ask"
table built on a join with a **median 12.5-second gap**, which at expiry is
meaningless; tightening to exact-tick matching reversed the finding entirely —
the limits are *above* the ask, not below. Each was caught by checking, none by
argument. So: the YES path returns zero on bids 5c through a deep book, the
order path has not changed in git, and I cannot name the mechanism. The
instrumentation gap is the finding — `place_order` returns the full payload and
`no_fill` records four fields of it.

**Vote: 3-0 to log the full Kalshi response plus a book snapshot on every
order. 3-0 to run `livecheck` for one contract. 3-0 AGAINST halting — misses
are free and halting destroys the evidence. 3-0 that if livecheck fills
normally, the answer is composition plus tape and this is not a defect.**

Falsifier, stated in advance: **a 1-contract YES bid 5c through a deep ask
either fills or it does not.** If it fills, the order path is exonerated and
the board should look at why the signal moved to YES. If it returns zero, the
board has a reproducible defect over real money and everything else waits.

**CHAIRMAN: pending.**
