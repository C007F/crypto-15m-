# Open questions — things the data likes that we have NOT acted on

Rule for this file: nothing here gets deployed on one session of evidence.
A finding graduates when it replicates across several days AND we have live
fills confirming it is reachable, not just visible.

---

## 1. Entry timing — edge is concentrated at the FRONT of the window

**Status:** measured, NOT deployed. Window deliberately left wide.

Measured 2026-08-26 from `research/observations.jsonl` (272 windows, ~8.5h,
causal log, no look-ahead). Buying the cheap leg inside the 0.02-0.50 band:

| remaining | n (independent windows) | actual | implied | edge | p | Bonferroni (17 tests) |
|---|---|---|---|---|---|---|
| 750s | 258 | 45.3% | 34.2% | **+11.1%** | 0.0001 | **0.001 — survives** |
| 600s | 266 | 35.0% | 28.5% | +6.4% | 0.0100 | 0.170 — no |
| 300s | 243 | 24.7% | 21.2% | +3.5% | 0.0916 | 1.000 — no |
| 150s | 193 | 19.2% | 19.6% | -0.5% | 0.4357 | 1.000 — no |

The 750s row is the only result in this project that has ever survived
multiple-comparison correction on genuinely independent samples. Plausible
mechanism: an early window is less informed than a late one — by 60s out
the market knows nearly everything.

**Why it is not deployed.** Every fill this bot has ever gotten came in the
last five minutes. We have ZERO live fills at 750s. "The book is deep at
750s" is not the same claim as "we can cross it there" — we were burned by
exactly that distinction with the stale summary quotes on 2026-08-25. And
the wide window earned +$8.79 live on 2026-08-26; a config that is working
does not get retuned on evidence weaker than its own track record.

**What would graduate it.** The observer replicating the 750s edge across
3+ days, plus live fills landing early once the window naturally allows
them. Then narrow in ONE step and measure — do not stack it with other
changes.

**How to act on it when the time comes.** `ENTRY_CUTOFF_SEC` in `.env`.
Width = 900 - ENTRY_START_SEC - ENTRY_CUTOFF_SEC.
Current: start 90, cutoff 60 -> 750s wide (full window).
The narrow version tested was cutoff 480 -> 330s wide. It was reverted.

**Do not** move this knob and something else in the same deploy. It was
moved three times in one day (150 -> 60 -> 480 -> 120 -> 60) and the churn
made it impossible to attribute anything.

---

## 2. Price band boundaries are soft at the edges

`MIN_COST=0.02` / `MAX_COST=0.50` are deployed but have never bound — zero
of the 35 overnight fills fell outside them. The floor is well-supported
(below 0.02 went 0-for-139 on the YES leg, -73% of stake pooled). The 0.50
ceiling rests on pooled data whose significance was inflated ~2.8x by
treating 10 checkpoints per window as independent. Re-derive the ceiling
from one-row-per-window data before trusting it to bind.

---

## 3. Does our model add anything over "buy any cheap leg"?

The +11.1% at 750s is available to a strategy that buys EVERY cheap leg in
band. Our bot is selective — it requires `MIN_EDGE=0.08` over its own
`p_yes`. Nobody has tested whether that selection beats indiscriminate
buying. If it does not, the model is noise on top of a simpler edge and
should be removed rather than tuned.
