
---

## Post-mortem — 8/27→28: the 429 spiral (for the record; feeds Memos 003–005)

**What happened.** From ~22:45 CT 8/27 through the morning, all seven active
traders logged 429 storms — 33,000+ rate-limit errors across the live logs
(xrp 9.3k, doge 5.1k, sol 4.3k). Entries were overwhelmingly declined by the
book_stale gate, so the night's cost is measured in windows sat out, not (so
far as sampled fills show — book_age_ms ≤ 103ms on every fill inspected) in
blind fills. The spiral was still alive at review (sol: 32 429s in its last
300 log lines; eth/doge/sol current-window book files missing while the
stream process ran).

**Root cause chain — each link verified in code or journal:**
1. Memo 001 deployed 8/27 ~20:00 CT: POLL_SEC 2.0 → 0.5, 4x baseline frame
   rate (.env confirms POLL_SEC=0.5).
2. Under the higher baseline, the streamer's discover() calls began drawing
   429s. discover() swallows per-series failures (`except: continue`) and
   returns a PARTIAL market list.
3. The session loop treats absence as closure: any subscribed ticker not in
   the partial list is unsubscribed and its book file DELETED
   (stream.py cleanup). A 429ed discovery therefore destroys HEALTHY books.
   The 23:45-ET window's files were orphaned at close and never cleaned —
   the cleanup path has been degraded since ~22:45 CT.
4. Traders see the missing/stale file and herd to REST inside the same tick
   (they share the 3s freshness clock), which starves the next discover().
   The loop closes. Journal: 415 subscribe events since 8/27 20:00 against
   4 socket drops — churn, not disconnects.
5. Separately: client.orderbook_top() caches FAILED reads for 1.6s. At
   POLL_SEC 0.5 the next ~3 ticks read the cached None and with_book() falls
   back to the /markets summary quote with book_stale=False — the entry gate
   silently bypassed on exactly the pricing the flag was built to block.

**What held:** the book_stale gate declined every tick that actually saw a
failed read; FOK and the risk rails bounded everything else. Housekeeping,
not board matter: the zec trader has been dead weight since 8/24
(market_not_found, plus its own Coinbase 429 tracebacks) and KXNEAR15M is
streamed but has no trader.

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
