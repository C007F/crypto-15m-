"""Passive price-ladder observer: implied vs realized across cheap asks.

Watches every tick, records the cheapest ask each side touched per window
(only while there is enough time left for a realistic fill), then on
settlement writes one row per side with the outcome. Stakes nothing. After a
few hundred windows, win rate by price bucket tells us where - if anywhere -
Kalshi's cheap side beats its price, and LOTTO_MAX_ASK gets set from data
rather than argument.
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from crypto15m.domain import MarketQuote

MIN_REMAINING = 10.0          # ignore asks nothing could realistically hit
MAX_TRACKED_ASK = Decimal("0.30")


class LadderObserver:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "ladder.jsonl"
        self.state_path = data_dir / "ladder_state.json"
        self._open: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            self._open = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._open = {}

    def _save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._open), encoding="utf-8")
        tmp.replace(self.state_path)

    def observe(self, m: MarketQuote, remaining_sec: float) -> None:
        if remaining_sec < MIN_REMAINING:
            return
        rec = self._open.setdefault(m.ticker, {"yes": None, "no": None})
        changed = False
        for side, ask, size in (("yes", m.yes_ask, m.yes_ask_size),
                                ("no", m.no_ask, m.no_ask_size)):
            if ask is None or size <= 0:
                continue
            if not (Decimal("0") < ask <= MAX_TRACKED_ASK):
                continue
            prev = rec[side]
            if prev is None or float(ask) < prev["ask"]:
                rec[side] = {"ask": float(ask), "remaining": round(remaining_sec, 1)}
                changed = True
        if changed:
            self._save()

    def settle(self, ticker: str, result: str, now: datetime) -> None:
        rec = self._open.pop(ticker, None)
        if rec is None or result not in {"yes", "no"}:
            return
        rows = []
        for side in ("yes", "no"):
            if rec.get(side) is None:
                continue
            rows.append({
                "ts": now.isoformat(timespec="seconds"),
                "ticker": ticker,
                "side": side,
                "min_ask": rec[side]["ask"],
                "at_remaining": rec[side]["remaining"],
                "implied_p": rec[side]["ask"],
                "won": result == side,
            })
        if rows:
            with open(self.path, "a", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
        self._save()

    def report(self) -> str:
        """Win rate by price bucket, vs implied."""
        try:
            rows = [json.loads(l) for l in open(self.path, encoding="utf-8")]
        except OSError:
            return "no ladder data yet"
        buckets = {}
        for r in rows:
            key = min(int(r["min_ask"] * 100) // 3 * 3, 27)   # 3c buckets
            b = buckets.setdefault(key, [0, 0, 0.0])
            b[0] += 1
            b[1] += 1 if r["won"] else 0
            b[2] += r["min_ask"]
        lines = [f"{'ask':>7} {'n':>5} {'won':>5} {'realized':>9} {'implied':>8}"]
        for key in sorted(buckets):
            n, w, s = buckets[key]
            lines.append(f"{key:>4}-{key+2:<2}c {n:>5} {w:>5} {w/n:>8.1%} {s/n:>8.1%}")
        return "\n".join(lines)
