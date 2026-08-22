#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path("/home/cam/projects/crypto-15m-bot")
DATA = ROOT / "data"

# One server for every coin. Paths are resolved per request from ?asset=,
# so adding a coin to assets.py is the only step needed to see it here.
import sys
sys.path.insert(0, str(ROOT / "src"))
from crypto15m.assets import ASSETS  # noqa: E402


def records(key: str) -> dict:
    """Win/loss per book over EVERY closed trade.

    Counting only settlements produced a selection bias big enough to invert
    the answer: a book that cuts losers and holds winners showed 12-0 while
    losing money on three coins. A bail is a closed trade with realised P&L,
    so it counts. Newer journal rows carry "won" and "pnl"; older ones do not,
    and for those a bail is scored a loss - should_flatten only fires when the
    thesis has flipped hard against the position, so it sells below entry.
    """
    import sqlite3
    out: dict[str, dict] = {}
    jp = DATA / key / "journal.sqlite3"
    if not jp.exists():
        return out
    try:
        c = sqlite3.connect(f"file:{jp}?mode=ro", uri=True, timeout=2)
        rows = c.execute("select kind, payload from events "
                         "where kind in ('settle','invalidate')").fetchall()
        c.close()
    except Exception:
        return out
    for kind, payload in rows:
        try:
            d = json.loads(payload)
        except Exception:
            continue
        b = d.get("book")
        if not b:
            continue
        r = out.setdefault(b, {"wins": 0, "losses": 0, "bailed": 0,
                               "settled": 0, "won_pnl": 0.0, "lost_pnl": 0.0})
        if "won" in d:
            won = bool(d["won"])
        elif kind == "invalidate":
            won = False
        elif d.get("result") in {"yes", "no"}:
            won = d["result"] == d.get("side")
        else:
            continue
        if kind == "invalidate":
            r["bailed"] += 1
        else:
            r["settled"] += 1
        r["wins" if won else "losses"] += 1
        try:
            v = float(d.get("pnl"))
            r["won_pnl" if won else "lost_pnl"] += v
        except (TypeError, ValueError):
            pass
    return out


def paths(key: str):
    d = DATA / key
    return d / "status.json", d / "paper.log", d / "lessons.jsonl"


def live_assets():
    """Assets that have ever written a status file, newest activity first."""
    out = [{"key": "all", "label": "ALL", "running": True}]
    for k in ASSETS:
        st, log, _ = paths(k)
        if not st.exists():
            continue
        try:
            fresh = (time.time() - log.stat().st_mtime) < 20
        except OSError:
            fresh = False
        out.append({"key": k, "label": ASSETS[k].label, "running": fresh})
    return out

STRATEGY_GOAL = {
    "gate": {
        "strategy": "Blends every expert. Only clicks when leftover Yes/No odds still pay after fees.",
        "goal": "Win the most money with the fewest dumb trades.",
    },
    "micro": {
        "strategy": "Watches if BTC already moved but Kalshi odds have not caught up.",
        "goal": "Grab stale prices. Sit when the book is already updated.",
    },
    "momentum": {
        "strategy": "Follows the last 1, 3, and 5 minutes of BTC tape.",
        "goal": "Ride short bursts. Ignore the mix / book-lag story.",
    },
    "sit": {
        "strategy": "Never trades. Just holds $1,000.",
        "goal": "Control. If this is beating the others, the others are losing to fees.",
    },
}


def tail_lines(path: Path, n: int = 12) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = [ln.rstrip() for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    except OSError:
        return []
    return lines[-n:][::-1]


def load_lessons(n: int = 8, LESSONS: Path | None = None) -> list[dict]:
    if LESSONS is None or not LESSONS.exists():
        return []
    try:
        rows = [json.loads(ln) for ln in LESSONS.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except (OSError, json.JSONDecodeError):
        return []
    return list(reversed(rows[-n:]))


def tone_for(action: str) -> str:
    a = (action or "").lower()
    if a.startswith("bought yes"):
        return "buy"
    if a.startswith("bought no"):
        return "sell"
    if "holding" in a or "blocked" in a or "exit" in a or "flatten" in a or "lesson" in a:
        return "hold"
    return "sit"


def money(x: float) -> str:
    sign = "+" if x >= 0 else "−"
    return f"{sign}${abs(x):,.2f}"


def decorate(book: dict) -> dict:
    pnl = float(book.get("pnl") or 0)
    if pnl > 0.004:
        tag, cls = "PROFIT", "buy"
    elif pnl < -0.004:
        tag, cls = "LOSS", "sell"
    else:
        tag, cls = "FLAT", "sit"
    book["pnl"] = pnl
    txt = str(book.get("pnl_txt") or "")
    book["pnl_txt"] = money(pnl) if "$" not in txt else txt
    book["pnl_tag"] = book.get("pnl_tag") or tag
    book["pnl_class"] = cls
    book["tone"] = tone_for(book.get("action") or "")
    meta = STRATEGY_GOAL.get(book.get("id") or "", {})
    book["strategy"] = book.get("strategy") or meta.get("strategy") or ""
    book["goal"] = book.get("goal") or meta.get("goal") or ""
    book.setdefault("realized_txt", "—")
    book.setdefault("open_txt", "—")
    book.setdefault("pct_txt", f"{pnl:+.2f}%")
    book.setdefault("position_txt", "no open trade")
    book.setdefault("lesson_bar", "")
    return book


def build_all() -> dict:
    """Every coin pooled. Success rate needs sample size, and one strategy
    running on six markets is one strategy - the per-coin split is what makes
    each book look thin."""
    agg: dict[str, dict] = {}
    for key in ASSETS:
        for bid, r in records(key).items():
            a = agg.setdefault(bid, {"wins": 0, "losses": 0, "bailed": 0,
                                     "settled": 0, "pnl": 0.0})
            a["wins"] += r["wins"]; a["losses"] += r["losses"]
            a["bailed"] += r["bailed"]; a["settled"] += r.get("settled", 0)
        st, _, _ = paths(key)
        if not st.exists():
            continue
        try:
            raw = json.loads(st.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for b in raw.get("books") or []:
            agg.setdefault(b["id"], {"wins": 0, "losses": 0, "bailed": 0,
                                     "settled": 0, "pnl": 0.0})
            agg[b["id"]]["pnl"] += float(b.get("pnl") or 0)
            agg[b["id"]]["label"] = b.get("label", b["id"].upper())

    books = []
    for bid, a in agg.items():
        n = a["wins"] + a["losses"]
        pnl = a["pnl"]
        books.append({
            "id": bid, "label": a.get("label", bid.upper()),
            "wins": a["wins"], "losses": a["losses"], "closed": n,
            "settled": a.get("settled", 0), "bailed": a["bailed"],
            "win_rate": (a["wins"] / n) if n else None,
            "win_txt": f"{a['wins']}-{a['losses']}",
            "rate_txt": f"{a['wins']/n:.0%}" if n else "—",
            "pnl": pnl,
            "pnl_txt": ("+" if pnl >= 0 else "\u2212") + f"${abs(pnl):,.2f}",
            "pnl_tag": "PROFIT" if pnl > 0.004 else "LOSS" if pnl < -0.004 else "FLAT",
            "per_trade": (pnl / n) if n else None,
            "per_txt": (("+" if pnl >= 0 else "\u2212") + f"${abs(pnl)/n:.3f}") if n else "\u2014",
            "equity_txt": "", "action": "", "position_txt": "",
        })
    books.sort(key=lambda b: (b["win_rate"] is not None, b["win_rate"] or 0,
                              b["closed"]), reverse=True)
    for i, b in enumerate(books, 1):
        b["rank"] = i
    lead = books[0] if books and books[0]["closed"] else None
    return {
        "running": True, "asset": "all", "word": "ALL", "tone": "sit",
        "why": (f"{lead['label']} leads across six coins: {lead['rate_txt']} of "
                f"{lead['closed']} closed  \u00b7  {lead['pnl_txt']}") if lead else "No closed trades yet.",
        "clock": "—", "ticker": "", "spot": "—", "dist": "", "odds": "—", "odds_sub": "",
        "books": books, "experts": {}, "lessons": [], "tape": [],
        "halt": "CLEAR",
        "risk_sub": "pooled across every coin \u00b7 ranked by success rate",
    }


def build_board() -> dict:
    """Everything in one payload: pooled strategy ranking plus every coin."""
    pooled = build_all()
    coins = []
    for key in ASSETS:
        st, log, _ = paths(key)
        if not st.exists():
            continue
        d = build(key)
        coins.append({
            "key": key, "label": ASSETS[key].label,
            "running": d.get("running"), "clock": d.get("clock"),
            "ticker": d.get("ticker"), "spot": d.get("spot"), "dist": d.get("dist"),
            "odds": d.get("odds"),
            "books": [{k: b.get(k) for k in
                       ("id", "label", "rate_txt", "win_txt", "pnl_txt", "pnl_tag",
                        "closed", "settled", "bailed", "per_txt", "per_trade",
                        "equity_txt", "action", "rank")}
                      for b in d.get("books") or []],
        })
    coins.sort(key=lambda c: c["key"])
    return {"pooled": pooled["books"], "why": pooled["why"], "coins": coins}


def build(asset: str = "btc") -> dict:
    STATUS, LOG, LESSONS = paths(asset)
    running = False
    try:
        running = (time.time() - LOG.stat().st_mtime) < 8
    except OSError:
        running = False
    raw = {}
    if STATUS.exists():
        try:
            raw = json.loads(STATUS.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
    mkt = raw.get("market") or {}
    books = [decorate(dict(b)) for b in (raw.get("books") or [])]

    rec = records(asset)
    for b in books:
        r = rec.get(b["id"], {"wins": 0, "losses": 0, "bailed": 0, "settled": 0})
        n = r["wins"] + r["losses"]
        b["wins"], b["losses"], b["closed"] = r["wins"], r["losses"], n
        b["settled"], b["bailed"] = r.get("settled", 0), r["bailed"]
        b["win_rate"] = (r["wins"] / n) if n else None
        b["win_txt"] = f"{r['wins']}-{r['losses']}"
        b["rate_txt"] = f"{b['win_rate']:.0%}" if n else "—"
        # Wilson lower bound: 6-0 and 60-0 are both 100%, and only one of them
        # means anything. Used for ranking so a thin streak cannot top the board.
        if n:
            import math
            z, ph = 1.96, r["wins"] / n
            d = 1 + z * z / n
            centre = ph + z * z / (2 * n)
            margin = z * math.sqrt((ph * (1 - ph) + z * z / (4 * n)) / n)
            b["confidence"] = max(0.0, (centre - margin) / d)
        else:
            b["confidence"] = -1.0
        b["per_trade"] = (b["pnl"] / b["closed"]) if b["closed"] else None
        b["per_txt"] = (("+" if b["per_trade"] >= 0 else "\u2212") +
                        f"${abs(b['per_trade']):.3f}") if b["closed"] else "\u2014"

    # Rank by success rate as asked, tie-broken by sample size; books with no
    # settled trades sink to the bottom rather than floating on an empty 0%.
    books.sort(key=lambda b: (b["win_rate"] is not None, b["win_rate"] or 0,
                              b["closed"]), reverse=True)
    for i, b in enumerate(books, 1):
        b["rank"] = i
    leader = books[0] if books and books[0]["closed"] else None
    word, tone, why = "SIT", "sit", "Four paper accounts. Each has its own strategy and $1,000."
    if not running:
        word, tone, why = "OFF", "off", "Paper loop is not writing."
    elif not mkt:
        word, tone, why = "WAIT", "sit", "Waiting for the next window."
    elif leader:
        why = (f"{leader['label']} leads: {leader['rate_txt']} of {leader['closed']} "
               f"closed  ·  {leader['pnl_txt']}")

    p = mkt.get("p_yes")
    ask = mkt.get("yes_ask")
    bid = mkt.get("yes_bid")
    # The trader writes "spot"; older migrated status files say "btc".
    spot_px = mkt.get("spot", mkt.get("btc"))

    def px(v):
        """Sub-dollar coins need real precision: DOGE at 0.09132 against a
        0.0914 strike renders as '$0.09 vs $0.09' at two decimals, which hides
        the entire distance the bot is trading on."""
        if v is None:
            return None
        a = abs(v)
        d = 2 if a >= 100 else 4 if a >= 1 else 6
        return f"${v:,.{d}f}"
    strike = mkt.get("strike")
    dist = mkt.get("distance_bps")
    experts = [
        f"{name} {float(blob.get('p', 0)):.0%}  u={float(blob.get('u', 0)):.2f}"
        for name, blob in (raw.get("experts") or {}).items()
    ]
    lessons = raw.get("lessons") or load_lessons(8, LESSONS)
    return {
        "running": running,
        "word": word,
        "tone": tone,
        "why": why,
        "clock": mkt.get("clock") or "—",
        "ticker": mkt.get("ticker") or "",
        "spot": px(spot_px) or "—",
        "dist": f"strike {px(strike)} · {dist:+.1f} bps" if spot_px else "",
        "odds": f"{p:.0%} vs {ask:.0%}" if p is not None and ask is not None else "—",
        "odds_sub": f"gate P(up) vs Yes ask · book {bid:.0%}–{ask:.0%}" if bid is not None and ask is not None else "",
        "books": books,
        "experts": experts,
        "lessons": lessons,
        "tape": tail_lines(LOG, 10),
        "halt": "CLEAR",
        "risk_sub": "each book $1,000 start · $100 daily cap · 25% drawdown kill · losses get a written note",
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self) -> None:
        if self.path.split("?")[0] in {"/", "/status", "/status.json"}:
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            key = (q.get("asset") or ["btc"])[0].lower()
            if key not in ("all", "board") and key not in ASSETS:
                key = "btc"
            if key == "board":
                payload = build_board()
            elif key == "all":
                payload = build_all()
            else:
                payload = build(key)
            payload["asset"] = key
            payload["assets"] = live_assets()
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


def main() -> None:
    ThreadingHTTPServer(("127.0.0.1", 8800), Handler).serve_forever()


if __name__ == "__main__":
    main()
