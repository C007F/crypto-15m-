#!/usr/bin/env python3
"""Sample the streamed book against Kalshi's real orderbook. Stakes nothing.

Board Memo 014. On 2026-08-31 the live traders went 39 consecutive attempts
without a fill. Two hand-run samples bracketed the episode and showed the
streamed book shifted DOWN against truth - median 2c during the streak, with
10 of 49 samples past the 5c cross cushion, and 0.8c after it ended. That
shape breaks YES (bid = too-low ask + 5c never reaches the real ask) and
spares NO (its ask is overstated, so it always crosses).

The cause is NOT established. `Book.seq` is never assigned or read, so a
dropped orderbook_delta is undetectable - but the "corruption accumulates
over the window" mechanism was tested and REFUTED (error is flat across
0-360s, correlation -0.142). Lag under a fast tape is the other candidate and
cannot be separated from two hand-run samples.

So this process exists to make the next episode self-answering: it records
the streamed book beside the true book continuously, so the question is
decided by data captured DURING a failure rather than reconstructed after it.

Rate limit is shared with live trading and the account already takes ~195
429s/day, so this samples ONE ticker every SAMPLE_EVERY_SEC in round-robin -
about 180 calls/hour total, not per coin - and backs off hard on any 429.
It never places an order and never writes anything the traders read.
"""
from __future__ import annotations

import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("ASSET", "btc")

from crypto15m.config import load_settings          # noqa: E402
from crypto15m.kalshi.client import KalshiClient    # noqa: E402
from crypto15m.kalshi import stream as S            # noqa: E402

OUT = ROOT / "research" / "book_truth.jsonl"
BOOKS = ROOT / "data" / "books"
SAMPLE_EVERY_SEC = 20.0
MAX_BOOK_AGE_SEC = 3.0
SERIES = ["KXBTC15M", "KXETH15M", "KXSOL15M", "KXDOGE15M",
          "KXXRP15M", "KXBNB15M", "KXHYPE15M"]
# The traders cross at ask + CROSS_CUSHION. An error past that is the one
# that makes a YES buy structurally unfillable, so it gets its own flag.
CUSHION = Decimal("0.05")


def truth(client, ticker):
    ob = client.public_get(f"/markets/{ticker}/orderbook",
                           params={"depth": 3})["orderbook_fp"]
    nd = ob.get("no_dollars") or []
    yd = ob.get("yes_dollars") or []
    if not nd or not yd:
        return None
    # Kalshi returns resting BIDS per leg, ascending. yes_ask = 1 - best no bid.
    return (Decimal("1") - Decimal(nd[-1][0]),
            Decimal("1") - Decimal(yd[-1][0]),
            Decimal(nd[-1][1]))


def main() -> None:
    client = KalshiClient(load_settings())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    i = 0
    backoff = 0.0
    while True:
        series = SERIES[i % len(SERIES)]
        i += 1
        try:
            ticker = S.synthesize_window_tickers()[series]
            p = BOOKS / f"{ticker}.json"
            age = time.time() - p.stat().st_mtime
            local = json.loads(p.read_text())
            t = truth(client, ticker)
            if t is not None:
                tya, tna, tsize = t
                lya = Decimal(str(local["yes_ask"]))
                lna = Decimal(str(local["no_ask"]))
                row = {
                    "ts": time.time(), "ticker": ticker, "book_age_sec": round(age, 2),
                    "local_yes_ask": str(lya), "true_yes_ask": str(tya),
                    "local_no_ask": str(lna), "true_no_ask": str(tna),
                    "yes_err": str(lya - tya), "no_err": str(lna - tna),
                    "true_yes_ask_size": str(tsize),
                    # the only field that matters for a fill: does the cross
                    # cushion still reach the real ask?
                    "yes_unfillable": bool((lya + CUSHION) < tya),
                }
                with OUT.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row) + "\n")
            backoff = 0.0
        except FileNotFoundError:
            pass                      # window just rotated; not an error
        except Exception as exc:
            if "429" in str(exc):
                backoff = min(300.0, max(30.0, backoff * 2))
                print(f"429; backing off {backoff:.0f}s", flush=True)
            else:
                print(f"sample failed ({type(exc).__name__}: {exc})", flush=True)
        time.sleep(SAMPLE_EVERY_SEC + backoff)


if __name__ == "__main__":
    main()
