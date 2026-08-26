from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


def diagnose(entry: dict, *, pnl: Decimal, how: str, result: str = "") -> dict:
    """Turn any closed trade into a short, honest note - won or lost.

    Only recording losses made the book look better than it was: a book that
    cuts every bad position and holds every good one shows a perfect settled
    record while losing money. Wins are logged with what went right so the
    tags mean something in both directions.
    """
    won = float(pnl) > 0
    side = (entry.get("side") or "").lower()
    p = float(entry.get("p_yes") or 0.5)
    u = float(entry.get("uncertainty") or 0.5)
    ask = float(entry.get("yes_ask") or 0)
    bid = float(entry.get("yes_bid") or 0)
    dist = float(entry.get("distance_bps") or 0)
    left = float(entry.get("remaining_sec") or 0)
    tags: list[str] = []
    bits: list[str] = []

    if u >= 0.55:
        tags.append("traded_unsure")
        bits.append(f"clicked while unsure (u={u:.2f})")
    if side == "yes" and dist < -15:
        tags.append("fought_spot")
        bits.append(f"bought UP while BTC was {dist:.0f} bps under the strike")
    if side == "no" and dist > 15:
        tags.append("fought_spot")
        bits.append(f"bought DOWN while BTC was {dist:.0f} bps over the strike")
    if side == "yes" and ask >= 0.70:
        tags.append("paid_rich")
        bits.append(f"paid {ask:.0%} for YES — little leftover if wrong")
    if side == "no" and (1.0 - bid if bid else 0) >= 0.70:
        tags.append("paid_rich")
        bits.append("paid rich for NO — little leftover if wrong")
    if left and left < 180:
        tags.append("late_window")
        bits.append(f"only {int(left)}s left in the window")
    if how == "flatten":
        tags.append("cut")
        bits.append("thesis flipped, we cut")
    elif how == "settle":
        tags.append("resolved_right" if won else "resolved_wrong")
        if result:
            bits.append(f"window resolved {result.upper()}, we were {side.upper()}")
        else:
            bits.append("window resolved " + ("our way" if won else "against us"))
    if abs(float(pnl)) <= 0.15:
        tags.append("fees_ate_it")
        bits.append("size is fee/spread sized")

    if won:
        # Keep the caution tags off winners: a win taken while unsure is still
        # a win, and letting it raise the edge bar would punish good outcomes.
        tags = [t for t in tags if t in {"resolved_right", "fees_ate_it", "late_window"}]
        if not bits:
            bits.append("no single reason, it just went our way")
        why = "We won because we " + "; ".join(bits) + "."
    else:
        if not bits:
            bits.append("lost, no single smoking gun")
        why = "We lost because we " + "; ".join(bits) + "."
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "book": entry.get("book"),
        "ticker": entry.get("ticker"),
        "side": side,
        "pnl": f"{pnl:.4f}",
        "pnl_txt": ("+" if won else "\u2212") + f"${abs(pnl):.2f}",
        "won": won,
        "how": how,
        "result": result,
        "tags": tags,
        "why": why,
        "p_yes": p,
        "uncertainty": u,
        "distance_bps": dist,
    }


class Coach:
    """Notes on every closed trade. Repeat mistakes raise that book's edge bar."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lessons: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    self._lessons.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            self._lessons = []

    def record(self, lesson: dict) -> None:
        self._lessons.append(lesson)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(lesson) + "\n")

    def recent(self, n: int = 8) -> list[dict]:
        return list(reversed(self._lessons[-n:]))

    # The bar is judged on the last N closed trades, not on a time window.
    # Time windows fail both ways here: with absolute thresholds (>=3 of a
    # tag) and ~5 closes/coin/day, a 7-day window holds 30+ lessons and three
    # late_window losses are near-certain at a 20% hit rate - the penalty
    # becomes permanent by arithmetic. A count window self-scales: a busy day
    # cycles mistakes out quickly, a quiet weekend does not expire into
    # amnesia. The 7-day cap stays only as an outer bound so lessons from a
    # long-dead regime cannot linger through a drought. Nothing is deleted
    # from the journal either way.
    LESSON_WINDOW_TRADES = 15
    LESSON_SHELF_LIFE_DAYS = 7

    def extra_edge(self, book_id: str) -> Decimal:
        """If a book keeps making the same mistake RECENTLY, demand more edge."""
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=self.LESSON_SHELF_LIFE_DAYS)).isoformat()
        mine = [les for les in self._lessons
                if les.get("book") == book_id
                and str(les.get("ts") or "") >= cutoff]
        recent = mine[-self.LESSON_WINDOW_TRADES:]
        tags = Counter()
        for les in recent:
            if les.get("won"):
                continue          # winners must not raise a book's own bar
            for t in les.get("tags") or []:
                tags[t] += 1
        extra = Decimal("0")
        if tags["traded_unsure"] >= 3:
            extra += Decimal("0.03")
        if tags["fought_spot"] >= 2:
            extra += Decimal("0.04")
        if tags["paid_rich"] >= 3:
            extra += Decimal("0.03")
        if tags["late_window"] >= 3:
            extra += Decimal("0.02")
        if extra > Decimal("0.10"):
            extra = Decimal("0.10")
        return extra
