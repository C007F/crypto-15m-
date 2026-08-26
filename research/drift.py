#!/usr/bin/env python3
"""Measure the gap between /markets summary quotes and the real orderbook.

Every paper trade in this repo was filled at the summary ask. Live orders must
cross the true ask. The difference is a tax paper never paid, and it is charged
on entry, so it comes straight out of the edge. This samples it.
"""
import sys, json, time, argparse
sys.path.insert(0, "src")
from decimal import Decimal as D
from crypto15m.config import load_settings
from crypto15m.kalshi.client import KalshiClient
from crypto15m.kalshi.markets import parse_market
from crypto15m.assets import ASSETS

def sample(c, series):
    out = []
    try:
        ms = c.public_get("/markets", {"series_ticker": series, "status": "open", "limit": 6})
    except Exception:
        return out
    for raw in ms.get("markets") or []:
        stale = parse_market(raw)
        top = c.orderbook_top(stale.ticker)
        if not top:
            continue
        ya, ysz, na, nsz = top
        if not (0 < ya < 1 and 0 < na < 1):
            continue
        out.append({
            "ticker": stale.ticker,
            "yes_stale": str(stale.yes_ask), "yes_true": str(ya),
            "no_stale": str(stale.no_ask),  "no_true": str(na),
            "yes_drift": str(ya - stale.yes_ask), "no_drift": str(na - stale.no_ask),
            "yes_sz_stale": str(stale.yes_ask_size), "yes_sz_true": str(ysz),
            "no_sz_stale": str(stale.no_ask_size),  "no_sz_true": str(nsz),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
    return out

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--sleep", type=float, default=20.0)
    a = ap.parse_args()
    s = load_settings(); c = KalshiClient(s)
    rows = []
    for r in range(a.rounds):
        for key, spec in ASSETS.items():
            rows += sample(c, spec.series)
        if r + 1 < a.rounds:
            time.sleep(a.sleep)
    with open("research/drift.jsonl", "a") as f:
        for x in rows:
            f.write(json.dumps(x) + "\n")
    print(f"wrote {len(rows)} samples")
