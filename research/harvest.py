#!/usr/bin/env python3
"""Harvest labelled history from Kalshi: settled markets + per-minute candles.

The causal observer (observe.py) only sees windows going forward, which means
waiting days for a sample. Kalshi already holds the answer: /markets?status=
settled paginates backwards, and each market's candlesticks endpoint returns
one candle per minute with yes_ask and yes_bid. Join those to the settlement
result and you have exactly the calibration dataset, retroactively, thousands
of windows deep.

One row per (market, minute). Only fields knowable at that minute, plus the
outcome. No minimums, no revisions - same discipline as observe.py.

Usage:
    python3 research/harvest.py --pages 20          # ~200 windows per series
"""
import sys, json, argparse, time
sys.path.insert(0, "src")
from decimal import Decimal as D
from pathlib import Path
from crypto15m.config import load_settings
from crypto15m.kalshi.client import KalshiClient
from crypto15m.kalshi.markets import parse_ts
from crypto15m.assets import ASSETS

OUT = Path("research/history.jsonl")
PACE = 0.5   # seconds between candle fetches; traders own the rate limit


def seen_tickers():
    s = set()
    if OUT.exists():
        for line in OUT.open():
            try:
                s.add(json.loads(line)["ticker"])
            except Exception:
                pass
    return s


def harvest(c, series, pages, done, fh):
    cursor, n = None, 0
    for _ in range(pages):
        params = {"series_ticker": series, "status": "settled", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        r = None
        for attempt in range(6):
            try:
                r = c.public_get("/markets", params)
                break
            except Exception as e:
                if "429" not in str(e) or attempt == 5:
                    print(f"  {series}: list failed {str(e)[:70]}")
                    return n
                time.sleep(PACE * (2 ** attempt) * 4)
        if r is None:
            return n
        ms = r.get("markets") or []
        cursor = r.get("cursor")
        for m in ms:
            t = m["ticker"]
            res = (m.get("result") or "").lower()
            if t in done or res not in ("yes", "no"):
                continue
            close = parse_ts(m.get("close_time"))
            if close is None:
                continue
            ct = int(close.timestamp())
            cs = None
            for attempt in range(6):
                try:
                    cs = c.public_get(
                        f"/series/{series}/markets/{t}/candlesticks",
                        {"start_ts": ct - 960, "end_ts": ct + 60,
                         "period_interval": 1},
                    ).get("candlesticks") or []
                    break
                except Exception as e:
                    if "429" not in str(e):
                        break
                    time.sleep(PACE * (2 ** attempt) * 4)
            if not cs:
                continue
            # Deliberately slow. The live traders share this rate limit and
            # they get priority - a backfill is never worth a missed fill.
            time.sleep(PACE)
            strike = m.get("floor_strike")
            for k in cs:
                ts = k.get("end_period_ts")
                ask = (k.get("yes_ask") or {}).get("close_dollars")
                bid = (k.get("yes_bid") or {}).get("close_dollars")
                if ts is None or ask in (None, ""):
                    continue
                rem = ct - int(ts)
                if not (0 <= rem <= 900):
                    continue
                ya = D(str(ask))
                # NO ask is the mirror of the YES bid; both legs from one candle.
                na = (D("1") - D(str(bid))) if bid not in (None, "") else None
                fh.write(json.dumps({
                    "ticker": t, "series": series, "result": res,
                    "yes_won": res == "yes",
                    "remaining_sec": rem,
                    "yes_ask": str(ya),
                    "no_ask": (str(na) if na is not None else ""),
                    "yes_bid": str(bid) if bid not in (None, "") else "",
                    "volume": k.get("volume_fp"),
                    "open_interest": k.get("open_interest_fp"),
                    "strike": str(strike) if strike else "",
                    "close_ts": ct,
                }) + "\n")
            done.add(t); n += 1
        fh.flush()
        if not cursor or not ms:
            break
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=5)
    a = ap.parse_args()
    s = load_settings(); c = KalshiClient(s)
    done = seen_tickers()
    print(f"already have {len(done)} markets")
    with OUT.open("a") as fh:
        for key, spec in ASSETS.items():
            got = harvest(c, spec.series, a.pages, done, fh)
            print(f"  {spec.series:12} +{got} markets", flush=True)
    print(f"total markets now {len(seen_tickers())}")
