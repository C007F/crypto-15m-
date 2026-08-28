# Memo 007 replay — live-only microstructure study
*Run 2026-08-28T17:00:11.245516+00:00. No live expert was changed.*

## Sample
- Joined live fills to micro settles: **180**
- Clean (outside 01:05–04:45Z 8/28 Gaussian window): **174**
- Flagged degraded window: **6** trades, 1 wins, P/L $+2.47

## Win rate by day (live-only, all joined)
- 2026-08-25: 1/1 = 100.0%  95% CI [20.7%, 100.0%]  P/L $+2.30  Brier 0.34845
- 2026-08-26: 16/60 = 26.7%  95% CI [17.1%, 39.0%]  P/L $+31.66  Brier 0.17806
- 2026-08-27: 20/67 = 29.9%  95% CI [20.2%, 41.7%]  P/L $+35.81  Brier 0.22226
- 2026-08-28: 11/52 = 21.2%  95% CI [12.2%, 34.0%]  P/L $+5.13  Brier 0.1729

## Is the 8/28 drop distinguishable from noise?
- 8/26–27 pooled (clean): 36/127 = 28.3%  95% CI [21.2%, 36.7%]  P/L $+67.48
- 8/28 clean: 10/46 = 21.7%  95% CI [12.3%, 35.6%]  P/L $+2.66
- Confidence intervals **overlap** — the drop is suggestive, not significant.

## Remaining-time buckets (clean)
- 150-300s: 15/39 = 38.5%  P/L $+27.36  Brier 0.24216
- <150s: 24/122 = 19.7%  P/L $+24.23  Brier 0.17909
- >=300s: 8/13 = 61.5%  P/L $+20.85  Brier 0.19557

## Candidates (Brier on clean fills with a quoted implied)
Lower Brier is better. `pnl_if_skip_weak` skips the trade when |p-0.5|<0.08.
- `d_control`: Brier 0.19446  taken 146/174  P/L if skip-weak $+54.84
- `a_time`: Brier 0.18642  taken 164/174  P/L if skip-weak $+69.27 ← winner
- `a_regime`: Brier 0.19446  taken 146/174  P/L if skip-weak $+54.84
- `b_unc`: Brier 0.19446  taken 146/174  P/L if skip-weak $+54.84
- `c_both`: Brier 0.18642  taken 164/174  P/L if skip-weak $+69.27

**Winner for 24h shadow: `a_time`.** Live entries still use control.
Shadow logger (bot.py prediction payload `shadow_007`) records every candidate alongside live micro p_yes. Deploying a winner as the live expert is a separate chairman decision. Memo 002's 200-trade gate still applies.

## Standing corrections (from the memo)
- Live book reads microstructure directly; `gate()` is display-only.
- 8/28 was +$0.35 live, not −$7.19 (that mixed shadow rows).
- Degraded-window fills were +$2.47 on 6 trades; they are flagged, not fit.
