#!/usr/bin/env python3
"""Write Kalshi account truth to data/exchange.json for the dashboard.

The dashboard must never hold credentials. It is the web-facing process; if it
is ever compromised it should not be able to sign an order. So the signing
happens here, in a short-lived job, and the dashboard reads a plain file.
"""
import json, os, sys, time
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("ASSET", "sol")
OUT = ROOT / "data" / "exchange.json"


def main() -> None:
    from crypto15m.config import load_settings
    from crypto15m.kalshi.client import KalshiClient
    c = KalshiClient(load_settings())
    out = {"at": time.time(), "balance": None, "day": None,
           "settled": 0, "winners": 0, "open": 0, "error": None}
    try:
        bal = D(c.auth_get("/portfolio/balance")["balance_dollars"])
        out["balance"] = float(bal)
        try:
            st = json.loads((ROOT / "data" / "live_state.json").read_text())
            out["day"] = float(bal - D(st.get("start_equity") or bal))
            out["halted"] = st.get("halted") or ""
        except Exception:
            pass
        today = time.strftime("%Y-%m-%d")
        rows = [x for x in (c.auth_get("/portfolio/settlements",
                                       {"limit": 200}).get("settlements") or [])
                if str(x.get("settled_time", ""))[:10] == today]
        out["settled"] = len(rows)
        wins = 0
        for x in rows:
            cost = D(x["yes_total_cost_dollars"]) + D(x["no_total_cost_dollars"])
            rev = D(str(x["revenue"])) / 100 if x["revenue"] else D("0")
            if rev - cost - D(x["fee_cost"]) > 0:
                wins += 1
        out["winners"] = wins
        out["open"] = len(c.auth_get("/portfolio/positions")
                          .get("market_positions") or [])
    except Exception as e:
        out["error"] = str(e)[:150]
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(out))
    tmp.replace(OUT)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
