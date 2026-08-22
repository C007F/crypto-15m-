# Powerful Mind — Kalshi 15m BTC

Paper-first system for **Kalshi `KXBTC15M`** (same contracts as Coinbase Predictions 15m BTC).

## Frequency (decision)

**Look often. Trade rarely.**

The product is a 15-minute binary. There is no faster contract to grind. Extra fills inside a window mostly pay quadratic fees + spread. 96 windows/day × $0.02–0.08 round-trip would chew the daily cap without an edge.

What we do:

- Re-evaluate every poll (~1s)
- **At most one entry per window**
- Flatten only if the gate flips hard against a live position (`P(yes)` ≤ 0.35 if long Yes, ≥ 0.65 if long No) and we are not inside the last 75s
- No re-entry after flatten in that same window
- Size scales with leftover EV and (1 − uncertainty)

Not in scope: 5-minute markets, spraying every window.

## Brain (drop 2)

| Layer | What |
|---|---|
| Perception | 1m BTC + BTC candles, distance-to-strike, multi-horizon returns, vol regime, Yes mid / spread |
| Experts | baseline (remaining-vol), microstructure (book lag vs spot), momentum (1/3/5m tape), regime (BTC + vol), tabular logistic (Coinbase 15m bootstrap if `data/models/logistic.json` exists) |
| Gate | inverse-uncertainty mix on logits; disagreement **raises** uncertainty so we skip |
| Policy | EV after fees+slippage must clear min_edge + k×uncertainty |
| Risk | unchanged, still above everything |

Bootstrap labels are Coinbase 15m close vs open — **not** CF Benchmarks RTI. Treat tabular as a weak prior until paper fills accumulate.

Trader is still not started until Cam says so.
