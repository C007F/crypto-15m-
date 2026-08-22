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


def paths(key: str):
    d = DATA / key
    return d / "status.json", d / "paper.log", d / "lessons.jsonl"


def live_assets():
    """Assets that have ever written a status file, newest activity first."""
    out = []
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
    leader = max(books, key=lambda b: b["pnl"]) if books else None
    word, tone, why = "SIT", "sit", "Four paper accounts. Each has its own strategy and $1,000."
    if not running:
        word, tone, why = "OFF", "off", "Paper loop is not writing."
    elif not mkt:
        word, tone, why = "WAIT", "sit", "Waiting for the next window."
    elif leader:
        why = f"Best book: {leader['label']}  {leader['pnl_txt']}  ({leader['pnl_tag']})"

    p = mkt.get("p_yes")
    ask = mkt.get("yes_ask")
    bid = mkt.get("yes_bid")
    btc = mkt.get("btc")
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
        "spot": f"${btc:,.2f}" if btc else "—",
        "dist": f"strike ${strike:,.2f} · {dist:+.1f} bps" if btc else "",
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
            if key not in ASSETS:
                key = "btc"
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
