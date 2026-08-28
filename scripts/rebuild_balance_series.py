#!/usr/bin/env python3
"""Rebuild the balance curve at per-event resolution, pinned to truth.

The chart needs the ups and downs, and they are all in Kalshi's records:
every buy, sell, and settlement moved the balance. Raw cash-flow replay
drifts (it summed +74 on a +36 account once), so this pins the curve to the
known balance anchors: within each anchor-to-anchor span the event deltas
are replayed and any residual error is spread evenly across the span's
events, forcing the curve THROUGH every anchor. Shape from events, level
from Kalshi. Future points come from the live snapshot series untouched.
"""
import sys, json
from decimal import Decimal as D
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import os
os.environ.setdefault("ASSET", "sol")
from crypto15m.config import load_settings
from crypto15m.kalshi.client import KalshiClient


def ts_of(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def fetch_all(c, path, key):
    out, cursor = [], None
    for _ in range(30):
        params = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        r = c.auth_get(path, params)
        out += r.get(key) or []
        cursor = r.get("cursor")
        if not cursor or not r.get(key):
            break
    return out


def main():
    c = KalshiClient(load_settings())
    events = []   # (ts, delta)
    for x in fetch_all(c, "/portfolio/fills", "fills"):
        n = D(x["count_fp"])
        px = D(x["yes_price_dollars"]) if x["side"] == "yes" else D(x["no_price_dollars"])
        cash = -(n * px) if x["action"] == "buy" else (n * px)
        events.append((ts_of(x["created_time"]), float(cash - D(x["fee_cost"]))))
    for x in fetch_all(c, "/portfolio/settlements", "settlements"):
        rev = D(str(x["revenue"])) / 100 if x["revenue"] else D("0")
        if rev:
            events.append((ts_of(x["settled_time"]), float(rev)))
    events.sort()

    # balance anchors: the seeded daily anchors + live snapshot points
    anchors = []
    for l in (ROOT / "data" / "balance_history.jsonl").read_text().splitlines():
        if l.strip():
            a = json.loads(l)
            from zoneinfo import ZoneInfo
            t = datetime.fromisoformat(a["day"] + "T00:00:00").replace(
                tzinfo=ZoneInfo("America/Chicago")).timestamp()
            anchors.append((t, a["balance"]))
    cur = json.loads((ROOT / "data" / "exchange.json").read_text())
    anchors.append((cur["at"], cur["balance"]))
    anchors.sort()

    pts = [[anchors[0][0], anchors[0][1]]]
    for (t0, b0), (t1, b1) in zip(anchors, anchors[1:]):
        span = [(t, d) for t, d in events if t0 < t <= t1]
        raw_end = b0 + sum(d for _, d in span)
        resid = (b1 - raw_end) / len(span) if span else 0
        bal = b0
        for t, d in span:
            bal += d + resid           # error spread evenly -> hits b1 exactly
            pts.append([t, round(bal, 4)])
        pts.append([t1, b1])           # pin the anchor itself
    out = ROOT / "data" / "balance_series.jsonl"
    with out.open("w") as f:
        for t, b in pts:
            f.write(json.dumps({"ts": t, "balance": b}) + "\n")
    print(f"{len(pts)} points, {pts[0][1]} -> {pts[-1][1]} "
          f"(events {len(events)}, anchors {len(anchors)})")


if __name__ == "__main__":
    main()
