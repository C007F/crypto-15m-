# Board Memo 012 — The marginal trade, and one finding that did not survive
*Proposed 2026-08-31. Measurement only. No change requested in this memo.*

379 settled live micro positions since 8/26, joined from three sources: the
per-coin journals (`settle`), `fills.jsonl` (filled contracts), and the
`prediction` event nearest each entry (microstructure uncertainty).

---

## Correction first: sizing is NOT blind to the book

The Claude seat told the chairman this morning that `conviction_size()` sizes
"blind to what's resting." **That is wrong.** `pick_side()` caps first:

```python
contracts = max_contracts
if size > 0:
    contracts = min(contracts, size)      # costs/edge.py:104-106
```

`conviction_size()` then spends a *fraction* of an already depth-capped number.
The bot has never requested more than it saw resting. The `if size > 0` branch
that would silently disable the cap has **never fired**: zero zero-size quotes
in 69,548 observations. The seat retracts the claim.

This matters beyond the correction: it means the 8/31 fill collapse (90% → 31%)
cannot be a sizing fault. It was the race, on the fastest tape of the four days
measured (median 4.07bps against 2.35 on 8/29, which filled 100%).

---

## Finding that HOLDS: the smallest trades do not pay

| filled size | n | win% | total P/L | per-contract |
|---|---|---|---|---|
| **1** | 99 | **13%** | **−$4.29** | **−$0.043** |
| 3 | 15 | 13% | −$3.80 | −$0.084 |
| 4 | 31 | 26% | +$5.31 | +$0.043 |
| **5** | 176 | 26% | **+$70.37** | +$0.080 |
| 8 | 28 | 29% | +$11.45 | +$0.051 |

Size 5 is **92% of everything this book has earned**. Size-1 is 26% of all
positions and loses at half the win rate of everything above it.

The mechanism is not mysterious: you only get 1 contract when the depth cap or
the EV term already squeezed you there. **Size-1 is the marginal-signal bucket,
identified by the sizer itself.** It is a label the system already computes.

**Split-half, chronological:**

| | n | win% | P/L |
|---|---|---|---|
| size 1, first half (8/26–8/28) | 60 | 18% | −$0.04 |
| size 1, second half (8/28–8/31) | 39 | 5% | −$4.25 |
| size ≥2, first half | 129 | 29% | +$70.86 |
| size ≥2, second half | 151 | 21% | +$10.05 |

Size-1 is **≤ 0 in both halves**. Size ≥2 is positive in both. The direction
holds out of sample. Excluding size-1 entirely: **+$80.90 against +$76.62
actual, or +$4.28.** Modest, and in-sample even after the split-half check,
because dropping size-1 is not a clean knob — some of those entries would have
been size 5 on a thicker book.

---

## Finding that DOES NOT hold: uncertainty

The seat proposed that high-uncertainty entries lose. Pooled, they appear to:

| | n | win% | per-contract | avg size |
|---|---|---|---|---|
| u < 0.40 | 345 | 22% | +$0.062 | 3.95 |
| u ≥ 0.40 | 34 | 18% | −$0.058 | 4.24 |

**Split-half destroys it.** First half: n=24, 25% win, **+$2.47** — positive.
Second half: n=10, 0% win, −$10.84. The sign flips, and the entire negative
result is ten positions that happened to all lose. **The seat withdraws this
finding.** It is noise wearing a table.

One observation from it does survive, because it does not depend on the P/L
sign: the uncertainty haircut is **functionally inert**. `frac *= max(0.25,
1 - u)` should cut size ~41% at u=0.57 against u=0.27. The high-u bucket's
average size is **4.24 against 3.95** — slightly *larger*. The depth cap and
the EV term dominate; the uncertainty term is arithmetic that does not reach
the outcome. That is worth knowing whether or not high-u trades are bad.

---

## The number nobody asked about

| | size ≥2 P/L |
|---|---|
| 8/26 – 8/28 | **+$70.86** |
| 8/28 – 8/31 | **+$10.05** |

Same book, same rules, 151 positions against 129. Win rate 29% → 21%. Whatever
the marginal trade is worth, **it is small against the fact that this book
earned seven times more in its first three days than its second three.** The
board should not let a $4 finding distract from that.

---

## Seats

**Musk seat — AGAINST any change, FOR the label.** You found a bucket the
system already computes that loses money, and the fix is one comparison. Fine.
But +$4.28 over six days is noise next to a book that went from +$70 to +$10 on
the same rules. Do not tune the sizer while the edge is doing that. If you want
to act, act on the thing that moved by $60, not the thing that moved by $4.

**Buffett seat — AGAINST, and note the seat corrected itself twice today.**
Two claims went to the chairman this morning; one was wrong on the code, one
was wrong on the data, and both were caught by checking rather than by
argument. That is the process working, and it is also a reason to wait. Memo
002's 200-trade gate exists for exactly this. Size-1 has 99 observations and a
$4 effect. Come back with 200 and I will look again. The split-half is the
right instinct and I want it on every finding from now on.

**Claude seat — AGAINST changing anything. I withdraw the uncertainty
finding and I retract the sizing claim.** What survives is narrow: size-1
entries are ≤0 in both halves, n=99, worth about $4. Falsifier, stated in
advance: if the next 100 size-1 entries come in positive, this is dead. I will
also name my own failure mode — I reported the uncertainty result before
splitting it, and the split reversed the sign. Pooled tables on n=34 should not
have reached the chairman.

**Vote: 3-0 to change nothing. 3-0 to journal a `marginal` flag on size-1
entries so the bucket accrues cleanly to 200. 3-0 that the six-day earnings
decline is the more important open question and deserves its own memo.**

**CHAIRMAN: pending.**
