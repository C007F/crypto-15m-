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

**CHAIRMAN: pending**

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

**CHAIRMAN: pending**
