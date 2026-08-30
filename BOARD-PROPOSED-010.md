
---

## Memo 010 — The exit path has been dead for three days
*Proposed 8/29. Recall, not a feature. Board asked to rule on what ships WITH the recall.*

### The defect

`src/crypto15m/trials.py:371` evaluates `self.settings.params`, which does not
exist. `Settings` is a frozen dataclass with 26 fields and no `params`; the
`getattr(..., "take_profit_net", 0.03)` default protects the **inner** name
while the outer attribute chain raises first. `bool(pos)` short-circuits, so it
fires only while a position is open — which is why it survived three days.

It shipped in **dcf391e (8/25 23:57, "Bank winners instead of riding every one
to expiry")**. The last `invalidate` event in any journal is 8/25 **22:56:59**,
sixty-one minutes *before* that commit. `scale_out` has **never been logged, in
any of the eight journals, once.** **1,299 crashes and climbing** across the
seven live traders (bnb 475, eth 228, doge 147, xrp 141, sol 116, hype 100,
btc 92). **244 settles** since, with no exit management of any kind.

The line was only ever meant to read a literal `Decimal("0.03")`. No `params`
object, no `TAKE_PROFIT_NET` env var, and no such Settings field has existed at
any point in repo history. The author wrote a config lookup against a config
system that was never built. It is the **only** defect of this shape in the
codebase — an AST pass over every `self.settings.X` in `src/` returns exactly
one unknown attribute.

### What it disabled — four exits, not one

Everything below line 371 is unreachable while a position is open: the
**scale-out** block, **`bankable()`** take-profit, **`edge_realised()`**
fair-value exit, and the pre-existing **`should_flatten()`** thesis-broke cut.
The AttributeError raises while *building* `bankable`'s last argument, so both
take-profit tests die together, and `should_flatten` dies with them because it
sits behind `took_profit` in the same expression.

Also dead: `guard.record_close`, the coach lesson path, `_review`, the
`_spent` holding-window early return, and every `self.save()` on those branches.

### Correction notice — the Claude seat's counterfactual was wrong, sign included

The seat reported that banking would have earned **+$2.76** over three days.
That analysis had **zero genuine post-entry observations — not most of them,
all 220.**

The reason is self-referential: `__main__` does `print(live.step())`, and
`step()` raises the instant a position exists, so **no tape line is ever
written while holding.** Every "post-entry tick" walked was either the bot's own
entry render line — the same quote the order crossed — or a line written
*before* the fill that sorts as "after" only because `live.log` floors seconds
while `fills.jsonl` keeps decimals. Of 15 fires: **8 were the entry frame, 7
were time-travel exits at prices that predate the purchase, 6 sat on a crossed
book.** The bug was measured with data the bug destroys.

Resampled against `research/observations.jsonl` — an independent observer
process that does not crash:

| | 3-day P/L |
|---|---|
| ride to expiry (actual) | **+$72.20** |
| bank 100% (claimed) | +$75.74 ❌ |
| **bank 100% (honest)** | **+$24.07 → −$48.13** |
| + `edge_realised` | −$47.15 |
| + `should_flatten` | −$53.54 |
| at true 0.5s resolution | **−$91 to −$88** |

Fire rate is **29.7%, not 7%**. Point estimate **−$45**, 90% CI **[−77, −21]** —
excluding zero, and excluding the claimed figure. Micro wins **24.2%** of the
time at **+$0.086/contract**: it lives on the right tail, and a 3c take-profit
sells the tail.

### `should_flatten` is not the safe half

It cuts a NO position when `p_yes >= 0.65`. Micro **buys** NO at a median
`p_yes` of **0.678**. **133 of 221 live entries (60.2%) were opened at a p_yes
that already satisfies the cut condition.** Replayed forward it fires on 48
positions a **median 2.1 seconds after entry**, costing ~**$22** of the $71.99
this book realized. Checked backward, micro's 232 pre-bug cuts fired at the
**same 2.1 seconds** and turned a +$7.98 hold into −$19.19 — costing **$22.01**.
Two independent datasets, one answer.

It would barely arm anyway: `should_flatten` refuses inside 150s while
`ENTRY_CUTOFF_SEC=60` opens positions past 60s, so **168 of 236 fills (71%) are
born permanently uncuttable.** `.env.example` still documents this hole; live
`.env` reopened it in commit 0d4f4d1.

### Collateral findings

- **Partial fills desync the ledger.** `_live_close` returns True on
  `closed >= 1` and discards the count; callers book the **full requested
  size**. A close filling 1 of 5 books a 5-contract exit while 4 real
  contracts ride to settlement unmanaged — the exact failure its own docstring
  exists to prevent. Never bitten because all 11 historical exits filled whole,
  only one under IOC. Arming banking makes it routine.
- **The order path is NOT stale.** `place_order` and `_live_close` are
  byte-identical to the version that last closed live on 8/25, and the entry
  path exercises the same call 223 times since 8/26. This was the board's
  largest fear and it is refuted.
- **Crossed books.** 3.6% of entry lines show `yes_bid > yes_ask` vs a 0.76%
  base rate (4.7×). `decide()` has no guard; a negative spread passes
  `spread > max_spread`. `bankable()` sells into `yes_bid`, so an armed exit
  fires into phantom bids.
- **You go blind whenever money is at risk.** 197 of 197 positions: ~379 tape
  lines before the fill, **zero after.** `status.json` freezes, so the
  dashboard freezes. The backoff ratchets 2→60s and never resets while a
  position lives; tick cadence goes 0.55s → 4.44s median, p90 34s, longest
  120s; **36% of positions had a blind stretch over 55s.** Because
  `guard.blocked()` runs once per `Mind.step()`, **LIVE_KILL is read once a
  minute over live money.**
- **Nothing was watching.** `watchdog.py:119` counts `"LIVE step failed"` as a
  healthy tick. The 103 test lines dcf391e added are `assertIn` checks on
  source text — they assert the broken line *exists*, so they go green because
  it is broken. No test anywhere drives `TrialBook.step()` with an open
  position.

### Change proposed to the board

Three options were put: **(a)** full fix, **(b)** risk-only restoring
`should_flatten`, **(c)** leave broken.

**Musk seat — AGAINST all three as written. FOR an amended (b).** The framing is
wrong. This is not a missing exit rule, it is a process that crashes and goes
blind the instant real money is on. bnb alone shows 2,241 backoff lines, 210
pinned at 60s. Median entry is 101s from close, so for the entire life of every
position the tape is dark, status.json is frozen, and LIVE_KILL — the best line
in this codebase — is read once a minute. Delete that today. It is one token.
Do not smuggle four untested rules in behind the recall. Full fix is −$45 on a
book that made +$72. `should_flatten` cuts 60.2% of entries at their own entry
thesis, median 2.1 seconds in — that is not a stop, it is a bug wearing a
stop's uniform. Fix the crash. Arm nothing.

**Buffett seat — AGAINST (a), (b) and (c) as written.** I came in believing the
loss-cut was the half with real evidence behind it. It is not, and I will say so
plainly. Two datasets, one answer, 2.1 seconds. Banking is worse: the +$2.76
rested on 15 events with zero genuine post-entry observations. This book wins
24.2% of the time at $0.086 a contract; it lives on the right tail, and a 3c
take-profit sells the tail. The 200-trade gate from Memo 002 applies here too —
arming needs 200 entries of *genuine* post-entry ticks, and arming is a separate
memo with its own vote.

**Claude seat — AGAINST (a), (b) as written, and (c). I own the error.** My
counterfactual had zero post-entry observations; my +$2.76 sits outside its own
confidence interval. I also called (b) the safe half and re-verified in-file
that it is not: 134 of 223 entries opened at a p_yes already satisfying the cut,
159 born uncuttable. Caveat against my own vote: 65 fires on coarse observer
marks is still thin evidence.

**Vote: 3-0 for the amendment — recall the crash, arm nothing.**

Replace the phantom lookup with a module constant and disarm **all four** exits
behind a single `TAKE_PROFIT_ENABLED = False`, so runtime behaviour is
byte-for-byte today's **minus the crash, the blackout and the 60s kill-switch
latency**. Journal every would-have-fired decision without acting on it.

**Conditions before ANY exit is armed (a separate memo and vote):**
1. `_live_close` returns the filled count; both callers reduce by it.
2. A full day of genuine post-entry ticks in the tape — data that has never
   existed. Buffett seat asks for 200 entries' worth.
3. Crossed-book guard in `decide()` and `bankable()`.
4. `should_flatten` thresholds re-expressed relative to the **entry** price,
   not absolute, and `ENTRY_CUTOFF_SEC` raised to 150.
5. `fee_multiplier` passed into `bankable()`'s two `taker_fee` calls.
6. `tests/test_trials.py::test_step_manages_an_open_position` — a test that
   fails on today's code.

**CHAIRMAN: pending.**
