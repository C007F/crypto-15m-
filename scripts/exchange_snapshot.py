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
        # halted flag still comes from the guard; the DAY number does not.
        try:
            st = json.loads((ROOT / "data" / "live_state.json").read_text())
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
        sets = c.auth_get("/portfolio/settlements", {"limit": 200})
        rows = []
        for x in (sets.get("settlements") or []):
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
        # Day-by-day ledger anchored to BALANCE, not derived accounting.
        # Settlement-based daily P&L double-counted cut positions (full cost,
        # zero recovery) and summed to -14 while the account was +36; a
        # cash-flow rebuild had its own hole. Cam called it: only the balance
        # is real. First reading of each Central day is snapshotted; a day's
        # P&L is the anchor-to-anchor difference net of ledgered deposits.
        hist_p = ROOT / "data" / "balance_history.jsonl"
        anchors = []
        if hist_p.exists():
            anchors = [json.loads(l) for l in hist_p.read_text().splitlines() if l.strip()]
        today_ct = datetime.now(CT).date().isoformat()
        if not any(a["day"] == today_ct for a in anchors):
            anchors.append({"day": today_ct, "balance": float(bal),
                            "source": "first snapshot of the day"})
            with hist_p.open("a") as fh:
                fh.write(json.dumps(anchors[-1]) + "\n")
        anchors.sort(key=lambda a: a["day"])
        daily = []
        for k in range(1, len(anchors)):
            span_days = (datetime.fromisoformat(anchors[k]["day"]).date()
                         - datetime.fromisoformat(anchors[k-1]["day"]).date()).days
            daily.append({
                "day": anchors[k-1]["day"],
                "pnl": round(anchors[k]["balance"] - anchors[k-1]["balance"], 2),
                "spans": span_days,      # >1 = anchors missing between, combined figure
                "prefix_era": anchors[k-1]["day"] < "2026-08-26"})
        # today so far = live balance vs today's anchor
        ta = next(a for a in reversed(anchors) if a["day"] == today_ct)
        daily.append({"day": today_ct, "pnl": round(float(bal) - ta["balance"], 2),
                      "spans": 0, "prefix_era": False})
        out["daily"] = list(reversed(daily))
        # THE definition of today's P&L. One source: balance vs today's anchor.
        # The guard's start_equity gave a second, subtly different "today";
        # two clocks was how the 7pm-reset bug hid.
        out["day"] = daily[-1]["pnl"] if daily else None
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
