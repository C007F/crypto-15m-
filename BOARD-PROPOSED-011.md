# Board Memo 011 — What is in the working tree, and what has authority to ship
*Proposed 2026-08-30. Review of 10 modified files + 4 untracked paths, none committed.*

The chairman asked the board to review the uncommitted tree before it becomes a
commit. It is not one change. It is **four decisions with four different levels
of authority**, plus data churn, and two of the four have no written ruling
behind them anywhere in `BOARD.md` or the memos.

---

## Item 1 — Memo 010 recall (`trials.py`, `tests/test_trials.py` +115)

`TAKE_PROFIT_NET` module constant replaces the phantom `self.settings.params`;
all four exits gated behind `EXITS_ARMED = False`; `_shadow_exit()` journals
what an armed exit would have done without acting. New tests drive
`TrialBook.step()` with an open position — the gap that let the defect live
three days.

**Authority: Memo 010, vote 3-0 for the amendment. `CHAIRMAN: pending`.**
**Deployed:** `trials.py` mtime 8/29 00:25:52, units restarted 00:27:46.

**It is working, and it is producing the data Memo 010 said had never existed.**

| | |
|---|---|
| shadow rows | **43** (bankable 19, thesis_flipped 15, edge_realised 9) |
| distinct positions observed | **27** |
| window | 8/29 05:42 → 8/30 06:44 UTC |
| `remaining_sec` at fire | median 201, range 16–695 |

These rows are **only writable while a position is open**. Their existence is
the proof that the blackout is over: Memo 010 measured 197 of 197 positions
with zero tape lines after the fill, and there are now 43 post-entry
observations in 25 hours. Condition #2 for arming ("a full day of genuine
post-entry ticks, which have never existed") has started accruing for the
first time.

---

## Item 2 — Memo 009 retry spacing (`trials.py`, `MIN_ATTEMPT_GAP_SEC = 2.0`)

**Authority: Memo 009, vote 3-0, `CHAIRMAN: FINAL`, executed 8/28 16:59.**
Clean. This is the one item on the tree with a completed ruling.

---

## Item 3 — ZEC re-admitted to live (`bot.py`, `test_live_guard.py`, `archive/zec-paper-2026-08-28/`)

`NO_LIVE_ASSETS` goes `{"zec","near"}` → `{"near"}`. The code comment and the
test docstring both assert *"Chairman re-admitted it 8/28 on remeasurement."*

**No such ruling exists in the record.** `grep -i zec BOARD.md` returns
**nothing**. There is no Memo 008. The only memo that mentions zec is 003-005,
which excluded it. The remeasurement argument survives only in the comment it
is used to justify — and `test_zec_is_no_longer_excluded` now asserts
`assertNotIn("zec", NO_LIVE_ASSETS)` under the docstring *"Reverting this is a
board decision,"* which converts an unrecorded change into a test-enforced one.

### The board re-ran the measurement. It substantially holds.

Recomputed from `research/observations.jsonl` (38,471 obs, 9 assets). Spread =
`yes_ask + no_ask - 1`. Entry window = `60 <= remaining_sec <= 150`.

| asset | windows | spread all | spread entry | ≤ MAX_SPREAD 0.08 | depth |
|---|---|---|---|---|---|
| btc | 393 | 1.0c | 0.2c | 100% | 228 |
| eth | 390 | 1.0c | 0.3c | 100% | 28 |
| sol | 386 | 1.0c | 0.4c | 99% | 22 |
| doge | 385 | 1.0c | 0.4c | 98% | 21 |
| xrp | 389 | 1.0c | 0.4c | 100% | 26 |
| bnb | 382 | 1.0c | 0.6c | 93% | 20 |
| hype | 384 | 1.0c | 0.4c | 100% | 23 |
| **zec** | 383 | **5.0c** | **1.8c** | **89%** | **30** |
| near | 325 | 5.0c | 1.6c | 84% | 26 |

Live seven pooled entry-window median spread: **0.40c** (n=7,199).

**Verified:** 4.9c overall (measured 5.0c). ~1.6c entry-window (measured 1.8c).
~0.4c for the live seven (measured 0.40c exactly). ~90% clearing MAX_SPREAD
(measured 89%). The "8c vs 1c" reading that excluded zec on 8/24 **does not
replicate** — the real entry-window ratio is ~4.5x, not 8x.

**Did not verify:** the depth claim. The comment says median depth 49 against
bnb's 10 (4.9x). Measured at the tighter ask it is **30 against bnb's 20
(1.5x)**. Definition-sensitive, but as written the comment overstates zec's
depth advantage by roughly threefold.

**NEAR's exclusion holds on its own terms:** 325 windows against btc's 393 =
**83%** coverage (comment says 76% on the smaller 8/28 sample). Same finding.

**Operationally this item is inert today.** There is no `crypto15m-live@zec`
unit running — seven units, zec is not among them. Committing it does not put
zec live. It removes the `SystemExit` that stops zec going live, so zec becomes
one `systemctl --user start crypto15m-live@zec` away from real money.

---

## Item 4 — Memo 005 part 2 flipped (`stream.py`, `test_stream_spiral.py`)

Subscribe now does `want = want | synth`. The stated reason is real and
operational: HYPE sat 15 minutes with no book file when `discover()` 429'd, and
the watchdog paged every rotate.

**This contradicts the written record in three places:**

1. Memo 009's own scope line: *"**Not in this memo:** signing `public_get`,
   flipping 005 part 2, promoting `a_time`, reverting 003-006."*
2. Memo 009, Claude seat: *"Do not mix in unsigned-`/markets` or
   calendar-synth in the same deploy — that would confound the scoreboard."*
3. `board/SESSION-2026-08-28.md`: *"005 part 2 still SHADOW."*

4. The test written to hold the line, `test_subscribe_path_does_not_union_synth`,
   has been **inverted** into `test_subscribe_unions_clock_tickers`, which now
   asserts `assertIn("want = want | synth", src)`.

`SHADOW_CALENDAR = True` still, but it no longer gates subscribe — only
`_record_calendar_shadow`. **The flag now misdescribes the code.** Anyone
reading `SHADOW_CALENDAR = True` will conclude synthesis is not driving
subscription. It is.

Side effect worth naming: the stream now holds subscriptions for **near and
zec** as well — `KXNEAR15M-...json` and `KXZEC15M-...json` are being written at
02:38 today — i.e. it is spending subscriptions on two coins that are not
traded, on an account whose scarcest resource is rate limit.

### The confound is not hypothetical. It is exact.

```
stream.py     mtime  8/28 17:01:22 CDT
stream.service start 8/28 17:01:23 CDT   <- one second later
POLL_SEC 2.0 executed 8/28 16:59    CDT   <- same restart
```

**The POLL revert and the 005p2 flip shipped in the same deploy, ~90 seconds
apart.** There is no segment of live data in which POLL 2.0 ran without the
synth union. Memo 009's scoreboard — *"next full day's first-shot fill % vs
94%"* — **cannot be read as designed**, because the thing its own Claude seat
warned against was done to it one minute after it was written.

### What survives the confound

Segmented on true deploy boundaries, not calendar days:

| segment | windows | filled | empty | first-shot | 3-attempt windows |
|---|---|---|---|---|---|
| glory (8/26 → 8/27 20:05) | 116 | 108 | 8 | 92% | **2** |
| A — POLL 0.5 only | 94 | 84 | 10 | 86% | **7** |
| B — POLL 2.0 + synth | 32 | 31 | 1 | 97% | **0** |
| C — B + 010 recall | 50 | 50 | 0 | 90% | **2** |

The **burst count** is the metric that survives. 009's mechanism claim was
specific: 0.5s poll dumps 3 IOCs into one flicker and `_spent` locks the
ticker. That is 7 burst windows under A against 2 in glory and 2 in C. A
subscription-set change cannot create or prevent a 3-IOC burst — the two
mechanisms do not touch. **009's mechanism is supported on evidence the
confound does not reach.** Its headline first-shot number is not cleanly
attributable, and B (n=32) and C (n=50) are too small to separate anyway.

---

## Also on the tree

- `scripts/watchdog.py` (+3) — do not page on a window that already closed.
  Small, correct, no memo needed.
- `research/observations.jsonl` +20,911 lines, `drive_backup.json`,
  `pending.json` — routine churn; this file is committed as a matter of course.
- Untracked: `BOARD-PROPOSED-009.md`, `BOARD-PROPOSED-010.md`,
  `board/SESSION-2026-08-28.md`. **The memos authorizing the tree are
  themselves uncommitted.**

## Two tests are red

Neither is caused by this work; both are fixtures outgrown by time.

- `test_paper_history_is_excluded` — asserts live closed trades `< 200` as a
  proxy for "no paper leak." Now 316. Its own comment records that this
  threshold already failed once on success (50 → 200). It has happened again;
  the proxy is not measuring what it names.
- `test_unsettled_overdue` — hardcodes `KXDOGE15M-26AUG280800-00` while
  `unsettled_overdue()` ignores anything closed >12h ago. Aged out on 8/29.

---

## Seats

**Musk seat — FOR 1, 2, watchdog, memos. AGAINST 3. FOR 4 only with the flag
renamed.** Two of these are already running on real money and exist in exactly
one place: this working tree, uncommitted. A power cut and the recall is gone
and the crash comes back. Commit them today, that is not a decision, that is
housekeeping. ZEC is the opposite: no unit is running, so committing it buys
**nothing today** and removes a guardrail for later. That is the worst trade on
the board — zero upside now, real downside on the day somebody types a start
command. Leave the guardrail in, write the memo, then remove it in the same
hour you actually start the unit. On 4: the ops problem is real and I would
rather have a confounded metric than HYPE dark for 15 minutes. But
`SHADOW_CALENDAR = True` sitting above code that unions synth anyway is a lie
in the source, and this codebase has already lost three days to a line that
said something it did not do. Rename the flag or delete it.

**Buffett seat — FOR 1, 2, watchdog, memos. AGAINST 3 and 4 as unrecorded.**
What is being committed here is not code, it is **authority**. Two of four
items claim a chairman ruling that is not written anywhere, and one of them has
been welded into a test that says reverting it is a board decision. That is how
a process stops being a process. I will say plainly that the zec *measurement*
looks good — 1.8c against 0.40c is a real fact and the 8c reading that
excluded it was wrong. That is an argument for **writing the memo**, not for
skipping it. And do not let anyone tell you 009 is settled: B is 32 windows and
C is 50. Glory was 116 and it printed 92%. We have not yet had one clean day of
anything. Circle of competence — we know 0.5s produced 7 burst windows. We do
not know what 2.0s-without-synth does, and after 8/28 17:01 we can no longer
find out from this data.

**Claude seat — FOR 1, 2, watchdog, memos. AGAINST 3 and 4 as written. I
correct myself on the deploy time.** Earlier today I told the chairman the
stream flip landed in the 8/29 00:27 restart. It did not — mtime 17:01:22,
service start 17:01:23, inside the *same* restart as the POLL revert. That
makes the confound worse than I described: not "8/29 is confounded" but "**no
live segment isolates the POLL revert at all.**" The claim I can still defend
is narrow and I will state its falsifier: burst windows 7 → 2 is attributable
to poll/gap because subscription breadth and the `_spent` 3-IOC lock share no
mechanism. If a future clean segment at POLL 2.0 shows burst windows back at 6+,
009's mechanism was wrong and I was wrong to certify it. On zec I verified four
of five claims and **the fifth failed**: depth is 30 vs bnb 20, not 49 vs 10.
A comment that overstates its own supporting number by 3x should not be the
only record of a live-money scope change.

**Vote: 4-0 to commit items 1, 2, the watchdog fix, and the memos.
3-0 to hold items 3 and 4 out of the commit pending a memo each.
3-0 that the two red fixtures be repaired, not deleted.**

Scoreboard for the next clean segment: **burst windows and first-shot at
POLL 2.0 with no further plumbing landing in the same deploy.** If anything
else ships alongside it, this board has no scoreboard at all.

**CHAIRMAN: ruling 2026-08-30.**

- **Items 1, 2, watchdog, memos — APPROVED, executed.** Commits 04f8252,
  70f3227, c759994, 0618946. The Memo 010 recall is now committed matter
  rather than a single uncommitted working tree.
- **Item 4 (Memo 005 part 2) — KEEP, and the board's revert recommendation is
  overturned.** The behaviour is correct: a 429 on `discover()` must not be
  able to leave a coin unsubscribed for a full window, which is what happened
  to HYPE. The Claude seat's case for reverting rested on restoring a clean
  scoreboard, and that case is weaker than it was argued: 009's *mechanism*
  claim is already settled by burst windows (7 under POLL 0.5 against 2 in
  glory and 2 now), a metric the subscribe set cannot touch. Only the
  first-shot percentage is confounded, and at n=32 and n=50 those segments
  were never going to settle anything. Reintroducing a real blind spot to
  clean up a thin number is a bad trade. **Condition attached and met:**
  `SHADOW_CALENDAR` is renamed `LOG_CALENDAR_COMPARE` and its comment,
  docstring and tests corrected, because a flag reading `SHADOW_CALENDAR =
  True` above code that subscribes anyway is the same defect shape as
  trials.py:371 - a line that said something it did not do.
- **Item 3 (zec) — STILL PENDING.** Held out of the tree. Needs its own memo,
  and the depth figure in the source comment must be corrected to 30 against
  bnb's 20 before it is used to justify anything.
