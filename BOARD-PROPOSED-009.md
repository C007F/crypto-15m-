# Board Memo 009 — Revert POLL_SEC 0.5 → 2.0
*Proposed 8/28 late afternoon CT. Fill-quality revert of Stage B's interval only.*

**Change:** `.env POLL_SEC=2.0`. Keep Stage B's stream-spot files and window
cache. Keep 003/005/006/004/flatten-skip. Commit and keep
`MIN_ATTEMPT_GAP_SEC=2.0` (already on the 16:34 CT processes). Restart the
7 live units at cutoff. No sizing, pricing, 0.65, or min_edge change.

**Why:** Memo 001's revert trigger was fill quality. Window-level live fills:

| period | windows | filled | empty windows | 3-IOC in ≤3s | first-shot |
| 8/26 | 63 | 59 | 4 | 0 | 94% |
| 8/27 pre-B | 58 | 56 | 2 | 0 | 95% |
| 8/27 post-B | 7 | 7 | 0 | 0 | 100% (n too small) |
| 8/28 | 62 | 52 | 10 | 6 | 79% |

Empty book_age 14-40ms, order RTT ~28ms both eras. Not 003, not flatten.
Root: POLL 0.5 dumps 3 IOC retries into one flicker then `_spent` locks the
ticker for the rest of the window. That pattern cannot happen at 2.0s.

The 2s attempt gap restores retry *spacing*. It does not restore first-shot
94% by itself. Putting poll back to 2.0 is the actual restore.

**Not in this memo:** signing `public_get`, flipping 005 part 2, promoting
`a_time`, reverting 003-006.

---

**Musk seat — FOR.** You bought speed to catch 13-second races and the
metric that paid you (+$35 days) was *fills landing*, not ticks printed.
0.5s turned three retries into a one-second suicide pact with `_spent`.
That's not first principles, that's a loop that fights itself. Revert the
interval, keep the eyes (stream files). The 2s gap stays so nobody can
re-arm 0.5s without the lock. If you want 0.5s later, tick fast and *fire*
every 2s — that's a different memo. This one is: stop lighting the money
on fire. Revert trigger already exists; you tripped it.

**Buffett seat — FOR, with the scoreboard.** Two days at 2.0s vs one day at
0.5s is not a career, but 6 burst-locks vs 0 is not noise, and first-shot
dropping 15 points is the thing 001 promised to watch. Do not unwind the
plumbing that stops you shooting a ghost book. Do not size up. Tomorrow's
first-shot fill %% is the only number that matters: toward 90%+ and this
was the right undo; still ~80% and we were wrong about the interval being
the whole story. Circle of competence: we know how 2.0s behaved. We do not
yet know that 0.5s-plus-gap equals 2.0s.

**Claude seat — FOR.** Claims that outrun evidence: 8/28 P/L does *not*
prove this (007: WR CIs overlap). Fill *quality* does: first-shot 94/95 →
79, burst-empty windows 0 → 6, same book age and order_ms. Overnight Stage
B n=7 cannot save 0.5s. Falsify: after this revert, a full day at 2.0s with
first-shot still ≤80% means the interval was not the root. Keep the 2s gap
measured. Do not mix in unsigned-/markets or calendar-synth in the same
deploy — that would confound the scoreboard.

**Vote: 3-0 FOR.** Revert trigger from 001 already written. Scoreboard:
next full day's first-shot fill %% vs 94%.

**CHAIRMAN: FINAL — executed 2026-08-28 16:59 CT.** POLL_SEC=2.0 on disk; 7 live units restarted at cutoff. Not committed, not pushed. GitHub origin left untouched.

---

## Chairman question (8/28): will this return 8/26–8/27? Undo anything that isn't plumbing.

**What those days actually were (git + fills, not vibes):**
- POLL_SEC **2.0** until Memo 001 FINAL **8/27 20:05 CT**
- Stage A (8/27 08:12) already had Coinbase spot on WS — the +$35 day ran with that
- Live book: micro only, 0.65, $15 daily, 8-cap, coach **+0.02 already on at EOD both green days**
- First-shot fill **94–95%**, empty windows **2–4**, **zero** 3-in-1s locks
- P/L **+$37 / +$35** at ~29–30% WR on a chop tape

**What we will not undo (plumbing / already-present on glory days):**
- 003 stale-book gate, 005 don't-delete-on-429, 006 history persist, 004 watchdog, flatten-skip
- Streamer (Stage A) — it was live during 8/27's green day
- Coach +0.02 — same bar as glory EOD
- 2s retry gap — no-op at POLL 2.0; insurance if 0.5s comes back

**What we will undo (the non-plumbing miss):**
- POLL_SEC 0.5 → **2.0**

**Musk:** The glory was fills landing, not ticks. Reverting the interval is the undo. It does not print +$35 if the book is 80c YES — that was the tape. You get the *machine* back. Take it.

**Buffett:** Do not promise the ledger. Promise the fill process you measured. Two +$30 days is not a law of nature; it is 94% first-shot on a friendly tape. Restore 2.0s. If tomorrow first-shot is ~94% and P/L is still flat, the edge is the tape, not a remaining knob. Do not start ripping 003 because you're angry at 0.5s.

**Claude:** Will this return 8/26–27 *function*? Fill function: **yes, that's the hypothesis, falsifiable tomorrow.** Glory *P/L*: **not claimed.** 007 already said 8/28 WR CI overlaps 8/26–27. Same 0.65, same coach. The only material interval change is POLL. Overnight Stage B n=7 cannot argue for keeping 0.5s.

**Vote on the question: 3-0.** Restore POLL 2.0 as the hardest honest undo. Do not unwind plumbing. Do not promise +$35. Promise first-shot back toward 94%.

**CHAIRMAN: pending.**
