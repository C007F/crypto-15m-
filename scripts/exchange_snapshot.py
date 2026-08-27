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
        # Lifetime P&L against the capital ledger (data/capital.json). The
        # bot cannot move money, so every deposit/withdrawal is Cam acting
        # manually and gets a ledger row. profit = balance + withdrawn -
        # deposited: new capital is not counted as return, and withdrawing
        # does not erase return already earned.
        try:
            cap = json.loads((ROOT / "data" / "capital.json").read_text())
            dep = sum(D(str(e["amount"])) for e in cap["events"] if e["kind"] == "deposit")
            wd = sum(D(str(e["amount"])) for e in cap["events"] if e["kind"] == "withdraw")
        except Exception:
            dep, wd = D("200"), D("0")
        profit = bal + wd - dep
        out["total"] = float(profit)
        out["deposited"] = float(dep)
        out["withdrawn"] = float(wd)
        out["total_pct"] = float(profit / dep * 100) if dep else None
        try:
            st = json.loads((ROOT / "data" / "live_state.json").read_text())
            out["day"] = float(bal - D(st.get("start_equity") or bal))
            out["halted"] = st.get("halted") or ""
        except Exception:
            pass
        # "Today" is Cam's day, not the server's. Chester runs UTC, so a
        # naive date rolled his trading day at 7pm Central and split evening
        # sessions across two "days".
        from zoneinfo import ZoneInfo
        from datetime import datetime
        CT = ZoneInfo("America/Chicago")
        today = datetime.now(CT).date().isoformat()
        rows = []
        for x in (c.auth_get("/portfolio/settlements",
                             {"limit": 200}).get("settlements") or []):
            ts = str(x.get("settled_time", ""))
            try:
                d = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(CT).date().isoformat()
            except ValueError:
                continue
            if d == today:
                rows.append(x)
        out["settled"] = len(rows)
        wins = 0
        for x in rows:
            cost = D(x["yes_total_cost_dollars"]) + D(x["no_total_cost_dollars"])
            rev = D(str(x["revenue"])) / 100 if x["revenue"] else D("0")
            if rev - cost - D(x["fee_cost"]) > 0:
                wins += 1
        out["winners"] = wins
        # Per-asset P&L from settlements. The journal undercounts (cuts and
        # partial closes do not land in it cleanly), so the board's per-asset
        # numbers come from here, same source as the headline.
        per = {}
        for x in rows:
            t = x.get("ticker") or ""
            key = t[2:t.find("15M")].lower() if "15M" in t else "?"
            cost = D(x["yes_total_cost_dollars"]) + D(x["no_total_cost_dollars"])
            rev = D(str(x["revenue"])) / 100 if x["revenue"] else D("0")
            pnl = rev - cost - D(x["fee_cost"])
            e = per.setdefault(key, {"pnl": 0.0, "n": 0, "w": 0})
            e["pnl"] += float(pnl); e["n"] += 1
            if pnl > 0:
                e["w"] += 1
        for e in per.values():
            e["pnl"] = round(e["pnl"], 4)
        out["per_asset"] = per
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
