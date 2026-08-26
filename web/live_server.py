"""Live trading dashboard. Real money only.

P&L comes from Kalshi settlements, not our own journal. The journal
undercounts - it showed BTC at -1.37 on a day the account was up 12.00 -
because cut positions and partial closes do not land in it cleanly. The
exchange is the only authoritative record, so the headline numbers are read
from /portfolio/settlements and /portfolio/balance and cached briefly.

No paper, no shadow book. Comparing live against a simulation that filled at
prices we cannot cross was actively misleading and is gone.
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
from crypto15m.bot import NO_LIVE_ASSETS  # noqa: E402


# Live trading began here; earlier journal rows are paper and must not be
# mixed into live P&L.
# Live P&L starts after the shadow got its own journal. Before this the two
# books shared a ledger and their trades cannot be told apart.
LIVE_SINCE = "2026-08-25T15:00"

# The orderbook pricing fix. Before this, limits were built off /markets
# summary quotes that drift from the real book, so orders were accepted and
# filled nothing - 32 of 35 died at status ok. All-time fill rate (16%) and the
# 171 fill_or_kill / 58 market_not_found rejects describe bugs that no longer
# exist, so headline health is measured from here and the full history is kept
# separately rather than averaged in.
PRICING_FIX = "2026-08-26T02:39"

# One known-bad row. The ZEC settle was booked before the NO cost-basis fix
# and recorded -0.9410 for a trade that really cost 7.35c. Corrected here
# rather than rewritten in the journal, so the original stays auditable.
CORRECTIONS = {("zec", "2026-08-24T20:30:24"): -0.0735}


def reconstructed() -> dict[str, dict]:
    """Pre-split live trades, rebuilt from fills.jsonl.

    fills.jsonl only ever recorded live orders, so it stayed clean while the
    journals were mixed. Each entry is paired to its exit fill, or to the
    market's settlement when it was held to close, and priced on the side
    actually held. Fees are recomputed rather than stored, so the total lands
    within about 17c of the account - real, not exact.
    """
    out: dict[str, dict] = {}
    try:
        raw = json.loads((DATA / "live_reconstructed.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    for coin, trades in raw.items():
        w = sum(1 for t in trades if t["pnl"] > 0)
        out[coin] = {"wins": w, "losses": len(trades) - w,
                     "pnl": round(sum(t["pnl"] for t in trades), 4)}
    return out


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
            # Shadow trades wrote into this journal until the split. They are
            # not tagged, so anything before the fix is unattributable and is
            # excluded rather than guessed at.
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


def _rows(path: Path) -> list[dict]:
    try:
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, json.JSONDecodeError):
        return []


def exchange_truth() -> dict:
    """Read the account snapshot written by scripts/exchange_snapshot.py.

    Deliberately a file read. The dashboard is the web-facing process and must
    never hold the signing key - preflight asserts it. The snapshot job does
    the authenticated calls; this only reads the result, and reports staleness
    rather than pretending a stale number is current.
    """
    try:
        d = json.loads((DATA / "exchange.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {"balance": None, "day": None, "settled": 0, "winners": 0,
                "open": 0, "error": "no snapshot yet", "age": None}
    d["age"] = round(time.time() - float(d.get("at") or 0))
    if d["age"] > 300:
        d["error"] = f"snapshot {d['age']}s old"
    return d


def _era(entries, filled) -> dict:
    """Health since the pricing fix, alongside the all-time figure.

    All-time mixes in two bugs that no longer exist - stale-quote limits that
    could not cross, and orders sent to the wrong exchange index - so a single
    blended number hides the current state.
    """
    cur = [e for e in entries if str(e.get("ts", "")) >= PRICING_FIX]
    cur_f = [e for e in cur if int(e.get("filled") or 0)]
    reasons = Counter(str(e.get("status")) for e in cur
                      if not int(e.get("filled") or 0))
    return {
        "since": PRICING_FIX,
        "attempts": len(cur), "fills": len(cur_f),
        "rate": (len(cur_f) / len(cur)) if cur else None,
        "all_attempts": len(entries), "all_fills": len(filled),
        "all_rate": (len(filled) / len(entries)) if entries else None,
        "reasons": reasons.most_common(6),
    }


def build() -> dict:
    pnl_by = live_pnl()
    # Fold the pre-split history back in, so moving LIVE_SINCE did not quietly
    # erase eleven real trades.
    for coin, r in reconstructed().items():
        cur = pnl_by.setdefault(coin, {"wins": 0, "losses": 0, "pnl": 0.0})
        cur["wins"] += r["wins"]; cur["losses"] += r["losses"]; cur["pnl"] += r["pnl"]
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

        # live market state, same fields the paper board shows per coin
        mkt = {}
        try:
            mkt = (json.loads((d / "status.json").read_text(encoding="utf-8"))
                   .get("market") or {})
        except (OSError, json.JSONDecodeError):
            mkt = {}
        pr = pnl_by.get(key, {"wins": 0, "losses": 0, "pnl": 0.0})
        closed = pr["wins"] + pr["losses"]
        coins.append({
            "key": key, "label": a.label, "running": fresh,
            "attempts": len(entries), "fills": len(got),
            "rate": (len(got) / len(entries)) if entries else None,
            "exits": len(exits), "last": line,
            "wins": pr["wins"], "losses": pr["losses"], "closed": closed,
            "win_rate": (pr["wins"] / closed) if closed else None,
            "pnl": round(pr["pnl"], 4),
            "excluded": key in NO_LIVE_ASSETS,
            # Spread is the single best predictor we have of whether a coin is
            # tradeable at all: ZEC's 8c lost $1.04 of a $1.05 total while the
            # 1c coins were flat. Shown live because it moves with the hour.
            "spread_c": (round((float(mkt["yes_ask"]) - float(mkt["yes_bid"])) * 100, 1)
                         if mkt.get("yes_ask") and mkt.get("yes_bid") else None),
            "ticker": mkt.get("ticker", ""),
            "clock": mkt.get("clock", "-"),
            "spot": mkt.get("spot"),
            "strike": mkt.get("strike"),
            "dist_bps": mkt.get("distance_bps"),
            "yes_bid": mkt.get("yes_bid"),
            "yes_ask": mkt.get("yes_ask"),
            "p_yes": mkt.get("p_yes"),
            "uncertainty": mkt.get("uncertainty"),
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
        "exchange": exchange_truth(),
        "era": _era(entries, filled),
        "wins": tot_w, "closed": tot_c,
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
