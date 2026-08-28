
---

## Correction notice — three claims from this morning's verbal readout are withdrawn

Before the memos, the record must be straightened. This morning's readout to
the chairman contained three errors, each caught by re-deriving the numbers
from primary sources rather than trusting the first pass:

1. **"Last night lost −$7.19."** WRONG. That figure summed journal settle
   rows across the live AND shadow books. Live-only, reconciled to the
   exchange balance, **8/28 is +$0.35** (day-start 270.9072 → 271.2541 at
   15:00Z; exchange.json day pnl +0.35). The day was flat, not a loss.
2. **"The momentum expert dominated the ensemble and drove the NO bets."**
   WRONG. Since the 8/26 paper removal, BOOKS holds only `micro`, whose
   `pick_view` returns the **microstructure** expert's view directly
   (trials.py:71-74, 156-164). The gate's momentum-heavy mix is journaled and
   displayed but **does not drive live entries.** The 12.3× weight figure is
   arithmetically right and operationally irrelevant.
3. **"The 429 storm's Gaussian window caused the bad trades."** NOT
   SUPPORTED. Live entries placed inside the failed-empirical-layer window
   (01:05–04:45Z) numbered **6, went 1-for-6, and made +$2.47.** The losing
   came from the **healthy** window afterwards (04:45–15:10Z: 42 entries,
   8-for-41, −$2.06). The storm's real effect was sitting out — 6 entries in
   3.7 hours versus 42 in the next 10.4 — which agrees with the 003–005
   post-mortem's "windows sat out, not blind fills."

What survives is narrower and more honest: **the win rate fell (≈27–30% →
≈20%) while the money stayed flat, and no verified cause has been
established.** The bugs below are real code defects found while looking;
none of them is proven to have cost money last night.

---

## Post-mortem addendum — 8/27→28, the model side (corrected)

**The three days, live-only.** Money from exchange.json (reconciled to
balance); win rates from live fills matched to their settle rows; window
resolutions counted per distinct ticker.

| day | live settles | wins | win rate | live P/L | windows resolved |
|---|---|---|---|---|---|
| 8/26 | 60 | 16 | 26.7% | +$37.26 | 28 yes / 28 no |
| 8/27 | 67 | 20 | 29.9% | +$35.15 | 46 yes / 22 no |
| 8/28 (to 15:10Z) | 52 | 11 | 21.2% | **+$0.35** | 43 yes / 20 no |

**The directional exposure is real.** Live entries 01:30–14:45Z: **44 NO
against 3 YES** while 68% of windows resolved YES. At entry the model implied
a 36.3% NO-win rate (mean of 1−p_yes over 49 live NO fills); realized was
≈20%. All seven assets move together, so this is one correlated bet placed 47
times, not 47 independent ones.

**Why the money survived anyway:** the longshot structure. Winners pay 3–48×,
so a 20% win rate at these prices is roughly break-even by design. That is
the sizing discipline working — and it is also why a win-rate drop of this
size shows up in the trade log as a wall of red before it shows up in the
balance.

**What the 429 storm actually was.** Two full machine reboots (kernel
6.8.0-124 → 6.8.0-138; `uptime -s` = 8/27 23:18) on top of two deploy
restarts at 20:05:36 and 20:21:19 CDT. Each restart makes all seven traders
rebuild their 5-day empirical move distribution from scratch: **14,710
"history fetch failed" tracebacks** across the seven logs (bnb 1715 … doge
2816), peaking at **7,431 Coinbase candle 429s in the 04:00Z hour** alone.
Coinbase 429s ceased at ~04:44Z and have not recurred. **Kalshi-side 429s are
a separate and still-active problem** — 10,359 and climbing at 14:54Z, which
is 003–005's territory, not this memo's.

**Not established, and flagged as such:** why the win rate fell. Candidates
are (a) regime — a trending tape punishes a strategy that bets the book is
stale, and 8/26's 28/28 chop rewarded it; (b) the microstructure expert's
fixed 0.65 lag-correction and its uncertainty formula, which contains no
time-remaining or distance-to-strike term; (c) ordinary variance on a
20-trade-a-day longshot book. Memo 007 exists to distinguish these, not to
assume one.

**Also for the record:** KXDOGE15M-26AUG280800-00 closed 12:00Z, expected
settlement 12:05Z, and at 15:00Z Kalshi still returns `result: ""` — a NO×3
position open ~3 hours with no alert anywhere in src/ or scripts/. The market
prices YES at 0.999, so it will almost certainly settle −$0.93. The bot's
retry loop is correct and will book it whenever Kalshi rules.

---

## Memo 006 — Restart hardening: persist the brain, stop the stampede
*Proposed 8/28. Ops hardening. Loss-unproven — see part 4 and the vote.*

**Change — four parts, deployable separately. No sizing, pricing, cadence or
strategy change:**

1. **Persist the distribution.** EmpiricalMoves has zero disk persistence
   (history.py:32-37): every restart empties it and refetches up to 25
   paginated Coinbase pages per asset, ~175 across the herd, and any single
   failed page discards every page already fetched (history.py:51, 62-67).
   Write moves + built_at to data/history/<product>.json on success; load on
   start if <24h old.
2. **Back off failed builds.** `built_at` is set only on success and `stale`
   is `now - built_at > 86400`, so with built_at=0 a failing build retries
   **every tick** — 0.5s at current POLL_SEC, confirmed in the logs as
   consecutive tracebacks one tick apart (bot.py:143-144, history.py:79,
   85-86). Add 60s → 5m → 15m backoff with per-asset jitter.
3. **Fix two latent crashes and one blind spot.** (a) bot.py lines 149 and
   208 call `log.warning`, but `log` is never defined or imported in bot.py —
   verified by AST scan. Either handler firing raises NameError from inside
   the except block. (b) No logging config exists in the live path, so
   `log.info` build-successes and the `only N candles; not enough` warning are
   structurally invisible — zero occurrences in all seven logs. We cannot
   currently prove a rebuild ever succeeded, only that failures stopped.
4. **Decline instead of improvise — PROPOSED, NOT ASSUMED.** While the
   empirical layer is missing, the live-driving microstructure expert loses
   its 50/50 empirical blend and prices on the pure Gaussian
   (microstructure.py:24-28) that history.py's own docstring calls
   miscalibrated near the strike. The theory says that manufactures phantom
   NO edges. **The data does not support it yet: that window went 1-for-6 for
   +$2.47.** Part 4 is therefore proposed as a conditional — instrument first
   (count entries taken with no empirical layer), and flip the decline only
   if the counter shows a real exposure.

**Musk seat — FOR 1-3, AGAINST 4 for now.** Parts 1 and 2 are not a project,
they are an afternoon: write a file after a successful build, and don't retry
a failed download twice a second. Seven processes re-downloading identical
public candles on every restart and then hammering the supplier in unison is
a DDoS we aimed at ourselves. Part 3 is a straight recall — a crash handler
that crashes. But part 4 wants to stop trading based on a theory that the
data just contradicted: the "degraded" window was our best three hours of the
night. Fix the plumbing, instrument the exposure, and don't buy insurance
against a loss nobody has found.

**Buffett seat — FOR 1-3, FOR 4 as a conditional only.** I came into this
expecting to argue that a blind system must stand down, and I still believe
it — but the Claude seat's own numbers took my best evidence away, and I am
not going to vote for a decline rule on six trades. So: instrument it, and if
the counter shows we routinely fill while structurally blind, we revisit with
proof. What does concern me is the pattern behind all of this. Memo 001
shipped speed, Memo 005 is the bill for it, and now 006: three memos in
forty-eight hours all tracing back to *we restart this system constantly and
it degrades badly when we do.* Restart discipline is the underlying asset.
Also note the day was flat, not down — the sizing rails did their job and
that is the only reason this conversation is calm.

**Claude seat — FOR 1-3; FOR 4 as conditional, and I withdraw my own
loss-attribution.** I told the chairman this morning that the Gaussian
window caused the bad trades. It did not: 6 entries, 1 win, +$2.47, against
−$2.06 across 41 settles once the layer was healthy again. The mechanism is
code-proven and loss-unproven, exactly the caveat I insisted on for Memo 003,
and I should have run the attribution before speaking. Conditions: (i) unit-test
the load path (fresh loads, >24h rebuilds, corrupt file falls back to
rebuild-with-backoff); (ii) Memo-001-style drill — delete one asset's history
file, confirm backoff cadence and that entries continue, restore; (iii) part
3(b) lands **first**, since without logging config we cannot verify any of
this from the logs; (iv) part 4 ships as a counter only, and returns to the
board with the count.

**Vote: 3-0 FOR parts 1, 2, 3. Part 4: 0 FOR as a live decline; 3-0 FOR as
instrumentation-first, returning to the board with data.**

**CHAIRMAN: FINAL — executed 2026-08-28 10:57 CT.** Live units restarted onto persisted
history + backoff + logging + hist_ok counter. Part 4 remains instrumentation
only (no entry gate). No sizing, pricing, cadence, or strategy change. Code
on disk; git commit left unmade unless the chairman asks.

---

## Memo 007 — What actually drives live entries, and why the win rate fell
*Proposed 8/28. A study, not a change. Supersedes the withdrawn momentum theory.*

**Standing correction:** the live book reads the microstructure expert
directly; `gate()` output is display-and-journal only (trials.py:71-74,
156-164). Any future analysis of live behavior must start there. The gate's
(1−u)² weighting and momentum's flat u=0.25 remain worth fixing **if the gate
ever drives money again** — filed, not actioned.

**The real object of study — microstructure.py:29-36:**
```
p   = implied + 0.65 * (fair - implied)
unc = 0.2 + spread*2 + (0.35 if |lag| < 0.04 else 0.05)
```
The expert always bets 65% of the way against the book's own price, on the
premise that a gap between spot-implied fair and Kalshi's quote is staleness.
In a trending tape that gap is not staleness — it is the book being right. And
`unc` contains **no time-remaining and no distance-to-strike term**, so
conviction is identical at 8 minutes and at 80 seconds.

**Change:** an offline replay + shadow study. (1) Replay every recorded
prediction against its settle across **all** days on record — 8/26 chop
(28/28), 8/27 and 8/28 trend (68% yes) — scoring candidates on calibration
(Brier, reliability curve) and replayed live-only P/L: (a) the 0.65 push
scaled by regime or by remaining time, (b) `unc` given a
distance/expected-move term, (c) both, (d) unchanged as control. (2) Report
whether the win-rate drop is explained by regime, by the expert, or is inside
ordinary variance for a ~20-trade/day longshot book — with a confidence
interval, since 52 settles is a thin sample. (3) Winner runs 24h in shadow
logging both p_yes streams. Deploy is a separate chairman decision.

**Musk seat — FOR the study, AGAINST the framing.** Fine, replay it. But
notice what the code says: we take the market's price and walk 65% away from
it because a constant in a source file says the book is stale. That constant
was never measured; it was chosen. The interesting experiment is not tuning
0.65, it is asking whether the microstructure premise holds at all in a
trend — and the answer is cheap to get from data we already have. Run it this
week. If the premise fails in trending tape, the fix is a regime switch, not
a smaller constant.

**Buffett seat — FOR, and this is the memo that matters.** Two good days and
one flat day is not a track record, it is three coin flips with a story
attached. We do not know whether the edge is real, and today we learned we
also did not know which expert was placing our trades — that is a circle-of-
competence failure, not a modeling one. So: no changes, no size, replay
across all regimes, and the chairman's 200-trade gate from Memo 002 governs
here too. Money not lost compounds. A flat day that taught us the P/L we were
quoting was double-counted is a cheap education.

**Claude seat — FOR, with the sample-size caveat stated up front.** The
mechanism is verified in code; its P/L contribution is not isolated from
regime, and with 52 live settles on 8/28 the win-rate drop from 29.9% to
21.2% is roughly one standard error — suggestive, not significant. That is
precisely why this memo authorizes a replay and not an edit. Conditions:
(i) score on 8/26 and 8/27 as well — a fix that only rescues 8/28 is
curve-fitting; (ii) live-only accounting throughout, since conflating shadow
rows is the error that produced this morning's phantom −$7.19; (iii) exclude
or flag the 01:05–04:45Z degraded window so contaminated inputs do not drive
the fit; (iv) publish the confidence interval alongside any recommendation.

**Vote: 3-0 FOR the replay + shadow study. No live change authorized.**

**CHAIRMAN: FINAL — executed 2026-08-28 12:01 CT.** Replay ran live-only (180 joined fills).
Winner for 24h shadow: a_time (0.65 push scaled by remaining). Live expert
unchanged. 8/28 WR drop CI overlaps 8/26-27 (suggestive, not significant).
Report: research/memo007_report.md. Deploying a_time as the live expert is
a separate chairman decision; Memo 002's 200-trade gate still applies.

---

## Amendment to Memo 004 — settlement-overdue trigger
*Proposed 8/28, folds into the pending watchdog memo.*

**Change:** add trigger (e): any market holding an open position that is
closed but unsettled >15 minutes → alert. Zero new dependencies; the watchdog
already reads position and market state. Verified gap: `settle_stale`
(trials.py:507, called every tick from bot.py:233) skips a market whose API
result is empty with **no journal event, no log line, no counter**, and a
repo-wide grep for overdue/stuck/alert/notify/webhook across src/ and
scripts/ returns zero matches.

**Musk seat — FOR.** An alarm that doesn't cover "the exchange owes us money
and has gone quiet" isn't an alarm.

**Buffett seat — FOR.** Today it is $0.93 of DOGE. The day it is a real
position stuck through a weekend, this trigger is the difference between a
phone call and a surprise.

**Claude seat — FOR.** Measured today: closed 12:00Z, expected 12:05Z, still
`result: ""` at 15:00Z — ~3 hours silent while the dashboard showed "open."
Fifteen minutes is 3× Kalshi's own expected-expiration margin, so
false-positive risk is low.

**Vote: 3-0 FOR.**

**CHAIRMAN: FINAL — executed 2026-08-28 12:01 CT.** Folded into Memo 004 phase 1 as trigger
(e): closed market with an open live fill, unsettled >15 minutes.
