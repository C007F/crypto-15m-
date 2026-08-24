"""Live trading dashboard. Reads only files on disk - never credentials.

Deliberately separate from the paper board: the numbers that matter live are
fill rate and realised cut cost, not equity curves. Paper answered "is there a
signal". This answers "can we actually trade it".
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path("/home/cam/projects/crypto-15m-bot")
DATA = ROOT / "data"
import sys
sys.path.insert(0, str(ROOT / "src"))
from crypto15m.assets import ASSETS  # noqa: E402
from crypto15m.live import STAGES  # noqa: E402


# Live trading began here; earlier journal rows are paper and must not be
# mixed into live P&L.
LIVE_SINCE = "2026-08-24T18:00"

# One known-bad row. The ZEC settle was booked before the NO cost-basis fix
# and recorded -0.9410 for a trade that really cost 7.35c. Corrected here
# rather than rewritten in the journal, so the original stays auditable.
CORRECTIONS = {("zec", "2026-08-24T20:30:24"): -0.0735}


def live_pnl() -> dict[str, dict]:
    """Realised win/loss per asset, from settle and invalidate events."""
    import sqlite3
    out: dict[str, dict] = {}
    for jp in sorted(DATA.glob("*/journal.sqlite3")):
        coin = jp.parent.name
        rec = out.setdefault(coin, {"wins": 0, "losses": 0, "pnl": 0.0})
        try:
            c = sqlite3.connect(f"file:{jp}?mode=ro", uri=True, timeout=2)
            rows = c.execute(
                "select payload, ts from events where kind in ('settle','invalidate') "
                "and ts > ? order by ts", (LIVE_SINCE,)).fetchall()
            c.close()
        except Exception:
            continue
        for payload, ts in rows:
            try:
                d = json.loads(payload)
            except Exception:
                continue
            if d.get("book") != "micro" or d.get("pnl") is None:
                continue
            try:
                v = float(d["pnl"])
            except (TypeError, ValueError):
                continue
            v = CORRECTIONS.get((coin, ts[:19]), v)
            rec["pnl"] += v
            rec["wins" if v > 0 else "losses"] += 1
    return out


def shadow_pnl() -> dict[str, dict]:
    """What the same strategy earned with perfect fills, same ticks.

    Read from each coin's shadow account file rather than a journal: the
    shadow book is a full TrialBook, so its ledger already holds equity,
    realised P&L and trade count.
    """
    out: dict[str, dict] = {}
    for d in sorted(DATA.glob("*/shadow/accounts/micro.json")):
        coin = d.parents[2].name
        try:
            b = json.loads(d.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pf = b.get("portfolio") or {}
        try:
            start = float(pf.get("starting_equity") or 0)
            cash = float(pf.get("cash") or 0)
            held = sum(float(p["contracts"]) * float(p["avg_price"])
                       for p in (pf.get("positions") or {}).values())
            out[coin] = {"pnl": round(cash + held - start, 4),
                         "trades": int(pf.get("trades_today") or 0),
                         "open": len(pf.get("positions") or {})}
        except (TypeError, ValueError, KeyError):
            continue
    return out


def _rows(path: Path) -> list[dict]:
    try:
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, json.JSONDecodeError):
        return []


def build() -> dict:
    pnl_by = live_pnl()
    shadow_by = shadow_pnl()
    coins, orders = [], []
    for key, a in ASSETS.items():
        d = DATA / key
        log = d / "live.log"
        try:
            fresh = (time.time() - log.stat().st_mtime) < 20
        except OSError:
            fresh = False
        fills = _rows(d / "fills.jsonl")
        entries = [f for f in fills if not str(f.get("side", "")).startswith("exit_")]
        exits = [f for f in fills if str(f.get("side", "")).startswith("exit_")]
        got = [f for f in entries if int(f.get("filled") or 0)]
        for f in fills:
            f["asset"] = key
        orders += fills

        line = ""
        try:
            line = log.read_text(encoding="utf-8", errors="ignore").splitlines()[-1][:150]
        except (OSError, IndexError):
            pass
        pr = pnl_by.get(key, {"wins": 0, "losses": 0, "pnl": 0.0})
        sh = shadow_by.get(key, {"pnl": 0.0, "trades": 0, "open": 0})
        closed = pr["wins"] + pr["losses"]
        coins.append({
            "key": key, "label": a.label, "running": fresh,
            "attempts": len(entries), "fills": len(got),
            "rate": (len(got) / len(entries)) if entries else None,
            "exits": len(exits), "last": line,
            "wins": pr["wins"], "losses": pr["losses"], "closed": closed,
            "win_rate": (pr["wins"] / closed) if closed else None,
            "pnl": round(pr["pnl"], 4),
            "shadow_pnl": sh.get("pnl", 0.0),
            "shadow_trades": sh.get("trades", 0),
            "gap": round(round(pr["pnl"], 4) - sh.get("pnl", 0.0), 4),
        })

    orders.sort(key=lambda r: r.get("ts", ""))
    entries = [o for o in orders if not str(o.get("side", "")).startswith("exit_")]
    filled = [o for o in entries if int(o.get("filled") or 0)]

    # fill rate by how stale the book was when we decided
    buckets = defaultdict(lambda: [0, 0])
    for o in entries:
        age = float(o.get("book_age_ms") or 0)
        if not age:
            continue
        k = "<30ms" if age < 30 else "30-60ms" if age < 60 else "60-120ms" if age < 120 else ">120ms"
        buckets[k][0] += 1
        buckets[k][1] += 1 if int(o.get("filled") or 0) else 0

    lat = sorted(float(o["order_ms"]) for o in entries if o.get("order_ms"))
    state = {}
    try:
        state = json.loads((DATA / "live_state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass

    tot_w = sum(c["wins"] for c in coins)
    tot_c = sum(c["closed"] for c in coins)
    return {
        "coins": coins,
        "wins": tot_w, "closed": tot_c,
        "shadow_pnl": round(sum(c["shadow_pnl"] for c in coins), 4),
        "shadow_trades": sum(c["shadow_trades"] for c in coins),
        "gap": round(sum(c["gap"] for c in coins), 4),
        "win_rate": (tot_w / tot_c) if tot_c else None,
        "pnl": round(sum(c["pnl"] for c in coins), 4),
        "attempts": len(entries),
        "fills": len(filled),
        "rate": (len(filled) / len(entries)) if entries else None,
        "reasons": Counter(str(o.get("status", ""))[:44] for o in entries if not int(o.get("filled") or 0)).most_common(4),
        "buckets": [{"k": k, "n": v[0], "f": v[1]} for k, v in sorted(buckets.items())],
        "latency": {"median": lat[len(lat)//2] if lat else None,
                    "p90": lat[int(.9*len(lat))] if lat else None},
        "recent": [{k: o.get(k) for k in
                    ("ts", "asset", "side", "quoted_px", "filled", "fill_px",
                     "book_age_ms", "order_ms", "status")} for o in orders[-14:]][::-1],
        "state": {
            "stage": state.get("stage", 0),
            # read the real ceiling rather than a copy of it that can drift
            "max_contracts": STAGES[min(int(state.get("stage", 0)), len(STAGES)-1)][0],
            "orders_at_stage": state.get("orders_at_stage", 0),
            "realized": state.get("realized_today", "0"),
            "start_equity": state.get("start_equity", "0"),
            "killed": (DATA / "LIVE_KILL").exists(),
        },
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/status"):
            body = json.dumps(build(), default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


def main() -> None:
    ThreadingHTTPServer(("127.0.0.1", 8810), Handler).serve_forever()


if __name__ == "__main__":
    main()
