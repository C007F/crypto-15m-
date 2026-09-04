# Board Memo 015 — Does micro have an edge? The 200-trade gate is met.
*Proposed 2026-09-03. Chairman asked the question the machinery exists to answer.*

Memo 002 set a gate: **200 trades before drawing conclusions.** There are now
**553 settled live micro positions** since 8/26. The gate is met and the board
should answer rather than tune.

---

## 1. The verdict: no demonstrated edge

| period | n | win% | total | mean/trade | t |
|---|---|---|---|---|---|
| all since 8/26 | 553 | 21% | +$71.78 | +0.130 | **+1.50** |
| 8/26–8/27 | 124 | 28% | +$69.53 | +0.561 | +3.10 |
| **8/28 onward** | **429** | **19%** | **+$2.25** | **+0.005** | **+0.05** |

**429 trades since 8/28 have produced $2.25.** t = 0.05 is not a weak edge; it is
indistinguishable from zero. The full sample reaches only t = 1.50, which does
not clear significance either, and it is carried entirely by two days:

```
8/26 + 8/27 alone         +$72.41
every other day combined   -$8.32
```

Ten trading days. Two of them are the entire result. That is the finding.

## 2. The one hypothesis that survived proper testing

Errors in the streamed book are transient (Memo 014: 33 episodes, none longer
than ~60s) while a genuine dislocation persists. So: **require the edge to hold
for at least one prior tick before entering.** Cause-agnostic, and it does not
depend on resolving the `seq`-versus-lag question.

Recomputed from the tape (`p_yes`, book, `u`) at each entry, walking back in 2s
steps. It survives the era control:

| persistence | era | n | total | mean |
|---|---|---|---|---|
| 0 ticks | glory | 56 | +$37.89 | +0.677 |
| 0 ticks | **since 8/28** | **202** | **−$37.39** | **−0.185** |
| ≥1 tick | glory | 44 | +$25.89 | +0.588 |
| ≥1 tick | **since 8/28** | **195** | **+$30.86** | **+0.158** |

And it survives split-half **within** the dead era — sign consistent in all four
cells, which is more than any other hypothesis this project has produced:

```
first half   drop(0-tick) n= 88  -18.07  t=-1.21   keep(1+) n=110  +19.54  t=+0.97
second half  drop(0-tick) n=114  -19.32  t=-0.98   keep(1+) n= 85  +11.31  t=+0.65
```

## 3. And it is still a wash

```
                  actual    with filter   filter effect
glory 8/26-27     +63.78       +25.89        -37.89
since 8/28         -6.53       +30.86        +37.39
FULL SAMPLE       +57.25       +56.75         -0.50
```

**Minus fifty cents over 497 trades.** The filter would have destroyed the
glory days and rescued the dead week, netting to nothing. It does not find an
edge; it moves where the money lands. Reporting only the `-6.53 -> +30.86` half
would have been the fourth false finding of this project.

What it does show is real but narrower than it looks: **micro's profitability is
regime-dependent, and the filter is a proxy for regime, not for edge.** In one
regime the flash entries were the winners; in the other they were the losers.
Nothing here identifies which regime you are in *ahead of time*, and that is the
thing you would need.

## 4. What the board is not claiming

- Not that the strategy is proven dead. t = 1.50 on the full sample is
  consistent with a small real edge that 553 trades cannot resolve.
- Not that the engineering was wasted. The crash, the blackout, the 60s
  kill-switch latency and the book errors were all real defects, correctly
  found and fixed. **None of them moved the P/L.** Fill quality returned to
  90%+ and the money did not follow. That is itself informative: the losses
  were never mechanical.

---

## Seats

**Musk seat — STOP OR SHRINK. AGAINST arming the filter.** Four hundred and
twenty-nine trades for two dollars and twenty-five cents. You have spent a week
building superb instrumentation around a book that does not make money, and the
instrumentation keeps proving the machine is fine. That is the answer. The
filter nets minus fifty cents over the full sample — that is not a fix, it is a
different way of losing the same amount. Either turn it off, or drop to one
contract and stop paying tuition on size 8 for information you already have.

**Buffett seat — FOR the honest answer, and I said this on 8/28.** *"Two +$30
days is not a law of nature."* Ten days later, they still aren't. Everything
after those two days is minus eight dollars. The 200-trade gate existed exactly
so we would not argue about this, and 553 trades is past arguing. I want the
record to note what was bought: **$64 of profit and a definitive answer for
$200 of risk.** That is a good trade. The bad trade is the next one, where we
keep the size on because stopping feels like admitting something. Cut size to
minimum, run the filter in shadow, and let it earn its way back in.

**Claude seat — FOR shadow, AGAINST arming, and I nearly filed a fourth false
finding.** The era-controlled and split-half numbers on the persistence filter
are the strongest signal this project has produced, and I was one query away
from reporting `-6.53 -> +30.86` as a fix. The full-sample column is what kills
it. Falsifier, stated in advance and before any code moves: **shadow the filter
for one week — if `keep(1+)` does not beat `drop(0-tick)` by at least
$0.15/trade with consistent sign across both halves of that week, out of
sample, the hypothesis is dead and micro should be turned off.** I will also
say plainly: no defect I found this week changed the P/L, so my hit rate on
"this is why it isn't working" is zero for four.

**Vote: 3-0 that the 200-trade gate is met and no edge is demonstrated.
3-0 AGAINST arming the persistence filter. 3-0 to journal it in shadow for one
week. 3-0 to cut sizing to stage 0 (1 contract) while it runs — an undemonstrated
edge does not earn 8 contracts. 2-1 against turning micro off outright today,
Musk seat dissenting.**

**CHAIRMAN: pending.** The board notes this is the memo it was assembled to
produce, and that the answer is "no, not yet, and probably not."
