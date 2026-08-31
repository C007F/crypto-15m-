# Board Memo 014 — Full audit of the order and fill path
*Proposed 2026-08-31. Chairman directed a full audit and declined livecheck.
The chairman's read that the misses were not happenstance is upheld.*

Episode: **39 consecutive attempts with no fill**, last fill 13:12:08 UTC,
recovery at 15:28:14 UTC on its own. Account never at risk: an unfilled IOC
costs nothing. Balance **$280.95, +$12.53 today, +$80.95 on $200**.

---

## 1. Ruled out, verified live against the exchange

| | |
|---|---|
| stream process | 0 restarts since 8/28; book files 0–1s old |
| spot feed | fresh AND accurate — BTC 78400.24 vs Coinbase 78432.51 (~4bps) |
| liquidity | 6,017 contracts resting on BTC. Not thin. |
| order body | well-formed; `exchange_index: 2` matches the market; IOC, so partials allowed |
| self-trade prevention | no resting orders on the account |
| phantom fills | `portfolio_value: 0`, zero open — orders genuinely are not filling |
| `parse_fill` | handles both response shapes and both field names; not misreading |
| order path code | **unchanged in git since 8/28.** Not a regression. |

## 2. CONFIRMED — the streamed book is biased, and the bias has the exact
## shape that breaks YES and spares NO

49 samples against Kalshi's live orderbook, taken DURING the episode (~15:00):

```
bot_yes_ask - true_yes_ask :  median -0.0200   min -0.1400
bot_no_ask  - true_no_ask  :  median +0.0160   max +0.1400

past the 5c cross cushion:    YES 10/49        NO 0/49
```

The whole book reads **low**. Consequences, and all three observed symptoms
fall out of this one fact:

- **YES cannot fill.** The trader bids `ask + CROSS_CUSHION(0.05)`. When the
  book reads more than 5c low — 1 sample in 5 — the bid never reaches the real
  ask. Depth is irrelevant; it is structurally unfillable.
- **NO fills.** Its `no_ask` is *over*stated, so the trader bids higher than
  needed and always crosses. Today: NO 38%, YES 7%.
- **The signal flipped side.** A book reading low makes YES look cheap, so the
  bot generated YES entries — attempts went from 6–10/day to **45**. It was
  buying the side its own bad book invented, on the side that cannot fill.

`top()` itself is correct (`yes_ask = 1 - max(no bids)`, verified against the
same formula used for truth). The formula is right; the **contents** are wrong.

## 3. CONFIRMED — there is no sequence-gap detection at all

`stream.py:61,66` declare and initialise `Book.seq`. A grep of the entire repo
finds **no other reference**. It is never assigned, never compared. The handler
applies every delta unconditionally:

```python
elif ty == "orderbook_delta" and ticker:
    b = self.books.setdefault(ticker, Book())
    b.delta(msg)          # no gap check, no resync
```

Kalshi's delta channel carries a sequence number precisely so a consumer can
detect dropped frames and resnapshot. This is a real correctness hole and it is
true regardless of what caused today.

## 4. REFUTED — the mechanism the seat proposed for it

The seat told the chairman that a dropped delta corrupts the book progressively
until the next snapshot, i.e. once per 15-minute window. **Tested and false.**
225 samples:

| secs into window | n | mean abs err | max |
|---|---|---|---|
| 0–120 | 60 | 0.0087 | 0.05 |
| 120–240 | 100 | 0.0079 | 0.04 |
| 240–360 | 65 | 0.0072 | 0.06 |

`correlation(time into window, error) = -0.142`. Error is flat, if anything
slightly *decreasing*. Books do not progressively corrupt. Withdrawn.

## 5. OPEN — what actually shifts the book

Two measurements bracket the episode:

| when | book error | fills |
|---|---|---|
| ~15:00, mid-streak | median **2c**, 10/49 past cushion | 0 for 26 |
| ~15:40, after | mean **0.8c**, max 6c | recovered |

The bias tracks the failure. But that is two time points, and a fast tape is an
obvious common cause of both. The board cannot separate **dropped deltas** from
**lag under load** on this evidence. One detail cuts against simple lag: BTC was
*falling*, and a book that merely lags a falling market should read too **high**.
Ours read low. That is unexplained and the board declines to explain it.

## 6. The instrumentation gap that let this run

`book_age_ms` — the metric the traders log and the board has been reading all
week — is **file freshness, not content freshness**. Through the entire episode
it read 8–70ms and looked healthy while the contents were shifted by cents.
Nothing in the system compares the streamed book to the exchange. That is why
this took a hand-run sample during a live failure to see at all, and why the
board is left with two data points instead of a series.

**Shipped with this memo:** `scripts/book_truth.py` +
`crypto15m-booktruth.service`. Round-robins ONE ticker every 20s (~180 REST
calls/hour total, not per coin, on an account already absorbing ~195 429s/day),
backs off hard on 429, writes `research/book_truth.jsonl`, places no orders,
and writes nothing any trader reads. It records `yes_unfillable` — whether
`local_ask + 0.05` still reaches the true ask — which is the single field that
decides whether a YES buy was ever possible. Started, not enabled; it will not
come back on reboot without the chairman.

---

## Seats

**Musk seat — FOR the sampler, AGAINST fixing the seq hole today.** You found
the thing: the machine is trading off a book that is wrong by more than the
amount it crosses by, and it invented the trades it then could not make. That
is worth the day. But you do not yet know why the book is wrong, and patching
`seq` is a guess wearing a fix's uniform — the accumulation theory it rests on
was just tested and died. Measure for a day. The sampler costs 180 calls an
hour and answers it. Then fix the thing you can name.

**Buffett seat — FOR the sampler, and note what this cost and did not cost.**
It cost nothing in dollars. Not one cent was lost to 39 misses; the account is
up $12.53 today and $80.95 on the stake. What it cost is that the book became a
NO-only strategy for two days without a vote, and the coach has been learning
from a sample with every YES entry deleted from it. I want the record to show
the board reads `book_age_ms` as a health metric and it measures the wrong
thing. That is the real finding. A number everyone trusts that answers a
different question is worse than no number.

**Claude seat — FOR the sampler. Fourth theory today, and the first that
partially survived.** Retracted today: that `conviction_size` ignores the book;
that high-uncertainty entries lose; that limits were below the ask (a join with
a 12.5-second median gap); and now the accumulation mechanism. What survives
here survives because it was measured against the exchange rather than argued
from the source: a code hole you can read, and a bias with the right shape at
the right time. I am explicitly NOT claiming the hole caused the bias.
Falsifier: if `book_truth.jsonl` shows `yes_unfillable` staying near zero
through the next miss streak, the book is exonerated and the cause is elsewhere.

**Vote: 3-0 to run the sampler for a full day before changing any code.
3-0 AGAINST patching `seq` on the current evidence. 3-0 that `book_age_ms` be
relabelled or retired, since it measures file mtime and is read as book health.
3-0 AGAINST halting — misses are free and the episode is the evidence.**

**CHAIRMAN: pending.**
