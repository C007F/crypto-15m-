# The Board

Board review for every material change to the trading system.
**Chairman of the Board: Cam.** Nothing deploys until the chairman says **final**.

Members give independent takes; disagreement is preserved, not averaged.
- **Musk seat** — first principles, speed, asymmetric upside; hates process for its own sake
- **Buffett seat** — capital preservation, sample size, circle of competence; hates losing money
- **Claude seat** — the measured data; hates claims that outrun evidence

A proposal memo has: the change, each seat's take, the vote, and
`CHAIRMAN: pending` until the chairman rules. Rulings: **final** (deploy), **reject**, or
amendments. Deployed memos record the outcome for the post-mortem.

---

## Memo 001 — Stage B: 4x faster reaction loop
*Proposed 8/27. Scheduled 8/28 08:47 CT pending the chairman.*

**Change:** traders read spot from the streamer's files (REST fallback),
cache static window metadata, POLL_SEC 2.0 → 0.5. Reaction 2000ms → 500ms.
Revert trigger: price improvement compresses below +3c or fill quality drops.

**Musk seat — FOR.** The bot is blind 75% of every second and you're asking
whether to open its eyes. Obviously yes. The revert trigger makes this a
free option: cost of being wrong is one day's noise, cost of being slow is
every thin-hour race lost forever. Ship it, measure, stop philosophizing.

**Buffett seat — FOR, warily.** Normally I'd say your edge is analytical,
not fast — speed is other people's game. But this isn't buying speed to
compete; it's removing a self-inflicted blindfold, and the downside is
bounded by a stated revert rule. I'd note the streamer being one day old is
the real risk: you're adding a dependency, and dependencies fail on
weekends. The REST fallback must be tested, not assumed.

**Claude seat — FOR.** Data: 30 of 32 overnight fills succeeded but the
thin-hour misses (fresh book, 0 filled, in bursts) are lost races; 25ms
order path is wasted behind a 2000ms look interval. Stage A has streamed
cleanly since deploy. Condition: verify the stale-file fallback path fires
correctly before flipping (kill the spot streamer, confirm traders degrade
to REST, restart it).

**Vote: 3-0 FOR** with Buffett/Claude condition: fallback drill before flip.

**CHAIRMAN: FINAL — executed 8/27 20:0x CT.** Drill passed (streamer killed:
stream read returned None, REST delivered, full frame built; restored clean).
POLL_SEC 2.0 -> 0.5 deployed. Scoreboard: fills/day and price improvement
vs the 8/26-27 baseline, reviewed at the 8/28 morning checkpoint.

---

## Memo 002 — Per-asset contract caps shaped to measured depth
*Proposed 8/27.*

**Change:** replace the flat cap 8 with per-asset ceilings: BTC 25, ETH 15,
all others stay 8. Sizing logic unchanged (conviction still decides within
the cap). Risk rails unchanged ($15/day stop, $50 floor).

**Musk seat — FOR, and it's timid.** The books told you their capacity: BTC
holds 2,633 contracts at top and you're sending 8 — that's not conservative,
it's leaving the factory at 0.3% utilization. I'd go BTC 50, ETH 25. The
daily stop bounds the damage either way; the asymmetry is all upside.

**Buffett seat — AGAINST, for now.** The edge is 2.5 days old and BTC — the
coin you'd 3x — is one of your two flat-to-negative performers (+$2.13
lifetime, 2/7 settles). You'd be adding size precisely where the edge is
least proven. Depth says the market can absorb it; nothing yet says the
signal deserves it. Wait for the 200-trade gate. Money not lost compounds
too.

**Claude seat — SPLIT, propose amendment.** Both are right about different
coins. Depth supports bigger BTC/ETH orders (fill-rate data: 69% at 8 on
thin books, 98% at 5 - the flat cap is simultaneously too big for DOGE and
too small for BTC). But Buffett's point cuts: BTC's edge is unproven at
+$2.13. Amendment: cap by BOTH depth and demonstrated edge — ETH 15 (deep
book AND +$11.48 proven), BTC stays 8 until its own P/L earns it, others
stay 8. Revisit at the 200-trade gate.

**Vote: 1 FOR as written, 1 AGAINST, 1 for the ETH-only amendment.**

**CHAIRMAN: REMANDED 8/27 — resubmit at the 200-trade gate.** The chairman
sides with the Buffett seat's sequencing: size follows proof. The board will
re-take up per-asset caps when the post-fix sample reaches 200 settled
trades, with fresh numbers on (1) per-asset edge - especially whether BTC has
earned anything, (2) depth and fill rate at current size, (3) Stage B's
effect on both. No size changes before then.

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

## Memo 003 — Close the stale-book gate bypass
*Proposed 8/28. The tourniquet: smallest change, stops the known leak.*

**Change:** three edits in kalshi/client.py + one in policy/decision.py.
(1) orderbook_top() stops caching failures (equivalently: a cache-hit None
sets book_stale=True). (2) with_book() sets book_stale=True on EVERY path
that returns without a real book, including the malformed-price early
return. (3) decision.py logs a counted "declined: book_stale" line so the
gate's work becomes measurable instead of silent. No sizing, pricing, or
cadence changes.

**Musk seat — FOR.** This is a recall, not a feature; boarding it is process
worship, but fine. The flag was built to stop summary-quote pricing and a
cache shortcut un-built it at exactly the moment Stage B quadrupled the
tick rate. Fix today.

**Buffett seat — FOR.** The only memo that can stop a real-money leak. A
gate that stands open 3 ticks in 4 under stress is not a gate. The counter
in (3) is non-negotiable: next storm we should be able to SAY how many
entries were declined and how many slipped through, not argue about it.

**Claude seat — FOR, with the honest caveat.** No confirmed blind fill
exists in the sampled records — the hole is code-proven, loss-unproven, and
(3) is what makes that claim falsifiable going forward. Test: unit-test a
warm failure cache (mock a 429, tick twice inside 1.6s, assert the decision
declines both ticks), then the Memo 001-style drill: block the orderbook
route live, confirm every tick declines, unblock.

**Vote: 3-0 FOR.** No revert trigger needed — the change only declines more
under failure. Scoreboard: daily stale-decline count; gate-bypass count
(must be zero).

**CHAIRMAN: FINAL — executed 2026-08-28 11:43 CT.** Live units + stream restarted. Cache-hit
None now sets book_stale; with_book marks stale on every bookless path;
decision.py logs counted declined: book_stale. No sizing/pricing/cadence
change. Commit left unmade unless the chairman asks.

---

## Memo 005 — Break the REST death-spiral
*Proposed 8/28. The cure. 003 is the tourniquet, 004 the alarm.*

**Change — three parts, deployable separately:**
1. **Never delete on absence of evidence.** discover() reports per-series
   success/failure; the cleanup loop only unsubscribes/deletes tickers whose
   series was successfully listed and which are genuinely gone. A 429ed
   series keeps its books. This alone breaks the spiral's closing link.
2. **Compute the calendar instead of asking for it.** Window tickers are
   pure clock arithmetic (KXBTC15M-26AUG281015-15). Synthesize next-window
   tickers and subscribe by construction; demote discover() to a verifier.
   Strike metadata still costs one /markets read per window per series
   (~32 req/hr total). The subscribe path stops depending on the rate limit
   entirely.
3. **De-herd the fallback.** Stream writes a heartbeat file every loop;
   traders treat "heartbeat fresh + book file missing" as "window just
   rotated — wait one tick" instead of REST-rushing; ±0.15s per-trader
   jitter so seven fallbacks never land in the same second.

**Musk seat — FOR; ship 2 first.** The system polls an API to learn what
time it is. The ticker IS the clock. Part 2 deletes the failure mode rather
than handling it — the only kind of fix worth writing. 1 and 3 are hygiene.
Tonight.

**Buffett seat — FOR, in order 1 → 3 → 2, each with a drill.** Part 1
removes a self-inflicted wound and touches nothing else — safest first.
Part 2 is elegant NEW code in the load-bearing path, and elegant new code on
a Friday evening is how weekends die. Memo 001's drill discipline — kill it,
watch it degrade, restart it — applies to every part. For the record: Stage
B shipped and twelve hours later the system had its worst night. Speed
bought fragility; this memo is the bill, and it gets paid before Memo 002
ever comes back to the table.

**Claude seat — FOR, with measurement conditions.** Evidence already in the
journal (415 subscribes / 4 drops; healthy books deleted on partial
discovers). Conditions: (i) after part 1, subscribe events should fall to
~32/hr — instrument and confirm; (ii) part 2 runs 24h in shadow (synthesized
tickers logged against discover()'s answers) before it drives a single
subscribe; (iii) the Memo 001 scoreboard re-baselines after this lands —
last night poisoned the Stage B comparison window, so no Stage B verdict
until clean data exists.

**Vote: 3-0 FOR, sequenced 1 → 3 → 2 with part 2's 24h shadow.**

**CHAIRMAN: FINAL — executed 2026-08-28 11:43 CT.** Stream + live units restarted. Part 1:
discover reports per-series success; cleanup will not delete a 429ed series.
Part 3: stream heartbeat + one-tick REST defer + ±0.15s poll jitter. Part 2:
calendar synthesis runs 24h in shadow (data/calendar_shadow.jsonl) and does
not drive a subscribe. Flip SHADOW_CALENDAR only after the chairman reviews.

## Memo 004 — Storm watchdog: the system must be able to wake the chairman
*Proposed 8/28. The smoke alarm. Independent of 003/005.*

**Change:** scripts/watchdog.py on a user systemd timer (5 min), read-only
over what already exists: live.log tails, books/ mtimes, fills.jsonl, the
stream heartbeat from Memo 005. Fire on: (a) >20% of any trader's last-15-min
ticks 429/stale, (b) no current-window book for an active series >3 min
while the stream runs, (c) zero fills across all traders for 4 open-market
hours, (d) stream unit inactive. Alert = ntfy.sh push to the chairman's
phone + a line on the dash. **Phase 2** (separate flip, ≥3 days later, after
reviewing phase-1 alert log): (a)+(b) sustained 15 min auto-touches
per-asset LIVE_KILL — stand down, don't guess.

**Musk seat — FOR alerts, AGAINST auto-kill.** Page the human. A watchdog
that can halt the factory on a threshold invented last night WILL halt it on
a false positive the one weekend nobody is watching, and that costs every
window until Monday. Alerts have no such tail.

**Buffett seat — FOR both; phase 2 is the point.** Last night the system
knew it was blind for hours — thirty-three thousand times — and told no one.
The per-tick gate already stands down for one frame; standing down for the
evening after 15 solid minutes of the same signal is the identical principle
with memory. Alert-only assumes a charged phone at 3am. The LIVE_KILL file
is the best line in this codebase; let something use it.

**Claude seat — FOR, phased exactly as written.** Backtesting the
thresholds against last night: (a) fires within minutes of 22:45, (b) by
23:00 — seven-plus hours before a human looked. Phase-2 false-positive cost
is bounded and known (≈ $0.82 expectancy × ~4 windows/hr missed);
false-negative cost is another blind night. Condition: phase 1 is strictly
read-only, and its alert log is reviewed before phase 2 arms.

**Vote: 3-0 FOR phase 1; 2-1 FOR phase 2 (Musk seat against).**

**CHAIRMAN: FINAL — executed 2026-08-28 12:01 CT.** Phase 1 only: scripts/watchdog.py on a
5-min user timer, ntfy.sh + dash banner. Triggers (a)-(d) plus amendment (e)
settlement-overdue. PHASE2_AUTOKILL remains False; auto-LIVE_KILL is a
separate flip after reviewing data/watchdog_alerts.jsonl for >=3 days.

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

