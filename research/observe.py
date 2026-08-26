#!/usr/bin/env python3
"""Causal observation log: features known at time T, outcome at settlement.

Every dataset this repo has produced so far is contaminated:

  paper P/L   - filled at summary asks that cannot be crossed, at sizes the
                book would not give, with cut costs modelled at -5c against a
                measured -0.4c to -2c.
  ladder      - min_ask is the minimum across the WHOLE window, written at
                settlement. A losing side decays to 0.001, a winning side never
                gets cheap, so "cheap asks win 99% of the time" is look-ahead,
                not edge.
  live fills  - only 15, and all priced by the pre-orderbook path.

This writes one row per checkpoint per window with ONLY what was knowable at
that instant, and joins the settlement result afterwards. Nothing is minimised,
nothing is revised. It stakes nothing and runs beside the traders.
"""
import sys, json, time, os
sys.path.insert(0, "src")
from decimal import Decimal as D
from datetime import datetime, timezone
from pathlib import Path
from crypto15m.config import load_settings
from crypto15m.kalshi.client import KalshiClient
from crypto15m.kalshi.markets import parse_market
from crypto15m.assets import ASSETS
from crypto15m.perception.spot_feed import fetch_spot_quote

OUT = Path("research/observations.jsonl")
PENDING = Path("research/pending.json")
# Checkpoints in seconds-remaining. Fixed so every window is sampled the same
# way and buckets are comparable across assets and days.
# 870/840/810 cover the first 90s, which ENTRY_START_SEC currently skips
# and which no data has ever touched. Cheap to watch, stakes nothing.
MARKS = [870, 840, 810, 750, 600, 450, 300, 240, 180, 150, 120, 90, 60]
TOL = 12.0


def load_pending():
    try:
        return json.loads(PENDING.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_pending(p):
    tmp = PENDING.with_suffix(".tmp")
    tmp.write_text(json.dumps(p))
    tmp.replace(PENDING)


def spot_distance(spec, strike, cache):
    """bps from spot to strike, signed. None if spot is unavailable."""
    if not strike:
        return None, None
    key = spec.spot_product
    if key not in cache:
        try:
            cache[key] = fetch_spot_quote(key).last
        except Exception:
            cache[key] = None
    px = cache.get(key)
    if px is None:
        return None, None
    return float(px), float((px - D(str(strike))) / D(str(strike)) * 10000)


def main():
    s = load_settings()
    c = KalshiClient(s)
    pending = load_pending()
    while True:
        now = datetime.now(timezone.utc)
        spot_cache = {}   # one spot read per product per sweep
        for key, spec in ASSETS.items():
            try:
                ms = c.public_get("/markets", {"series_ticker": spec.series,
                                               "status": "open", "limit": 4})
            except Exception:
                continue
            for raw in ms.get("markets") or []:
                q = parse_market(raw)
                rem = (q.close_time - now).total_seconds()
                mark = next((m for m in MARKS if abs(rem - m) <= TOL), None)
                if mark is None:
                    continue
                seen = pending.setdefault(q.ticker, {"asset": key, "marks": {}})
                if str(mark) in seen["marks"]:
                    continue
                top = c.orderbook_top(q.ticker)
                if not top:
                    continue
                ya, ysz = top["yes_ask"], top["yes_ask_size"]
                na, nsz = top["no_ask"], top["no_ask_size"]
                spot, dist = spot_distance(spec, q.strike, spot_cache)
                seen["marks"][str(mark)] = {
                    "ts": now.isoformat(timespec="seconds"),
                    "remaining_sec": round(rem, 1),
                    "yes_ask": str(ya), "no_ask": str(na),
                    "yes_ask_size": str(ysz), "no_ask_size": str(nsz),
                    "yes_ask_stale": str(q.yes_ask), "no_ask_stale": str(q.no_ask),
                    "strike": str(q.strike), "spot": spot, "distance_bps": dist,
                }
                save_pending(pending)
        # settle anything that has closed
        for ticker in list(pending):
            try:
                m = c.public_get(f"/markets/{ticker}")["market"]
            except Exception:
                continue
            res = (m.get("result") or "").lower()
            if res not in ("yes", "no"):
                if parse_market(m).close_time < now.replace(tzinfo=timezone.utc):
                    continue
                continue
            rec = pending.pop(ticker)
            with open(OUT, "a") as f:
                for mark, row in rec["marks"].items():
                    row.update({"ticker": ticker, "asset": rec["asset"],
                                "mark": int(mark), "result": res,
                                "yes_won": res == "yes"})
                    f.write(json.dumps(row) + "\n")
            save_pending(pending)
        # Research yields to trading, always. At 5s this competed with seven
        # live traders for the same rate limit and they were taking 429s while
        # trying to place orders. Checkpoints are minutes apart; 20s misses
        # nothing.
        time.sleep(20)


if __name__ == "__main__":
    main()
