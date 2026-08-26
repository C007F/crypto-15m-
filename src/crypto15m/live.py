"""Live-trading guard rails. Nothing here is tunable, ever.

The paper books proved a signal. Going live tests one different question: do
the stale quotes micro finds actually fill? Orders go out fill-or-kill, so a
bad fill is impossible - you either get the quoted price or nothing. That
makes FILL RATE the measurement, not slippage.

Three hard limits and a kill switch. The tuner has no reference to this module.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

log = logging.getLogger(__name__)


# No step-up. Staging existed to learn fill rate at each size, but fill rate
# turned out not to be size-dependent: every fill so far - the bot's ZEC trade
# and three manual probes - was a single contract, and the rejections are
# price/timing, not depth. Meanwhile a flat cap of 1 was running a different
# strategy from the one paper measured, the same mistake as inheriting paper's
# lessons.
#
# Conviction sizing does the work instead: it spends a fraction of the ceiling
# based on leftover EV and (1 - uncertainty), so ordinary signals still trade
# small and only a strong, agreed one reaches the top.
#
# The ceiling is account-scaled, not copied. Paper ran $1000 a book with a max
# position near $15, about 1.5%. On $200 that is roughly $3, or 8 contracts at
# 50c - and six coins at once is then ~9% of the account rather than the 45%
# a straight copy of paper's 30 would have meant.
# (max_contracts, filled_orders_needed_to_advance). 0 = terminal.
# Restored after the orderbook pricing fix: until then limits landed under the
# true ask and almost nothing crossed, so an 8-contract request was harmless.
# Now that orders fill, 8 spends the $15 daily cap in three trades - the fewest
# observations at the moment we most need them. Ramp so the cap reaches 8 with
# ~20 filled trades of evidence behind it instead of none.
STAGES: tuple[tuple[int, int], ...] = (
    (1, 10),
    (3, 10),
    (8, 0),
)


@dataclass
class LiveLimits:
    """Dollar limits, not percentages. A percentage of a small account is a
    rounding error, and the cap has to be legible at a glance."""

    daily_loss_usd: Decimal = Decimal("15")
    max_contracts_hard: int = 8          # ceiling regardless of stage
    max_open_positions: int = 6          # one per coin
    min_equity_usd: Decimal = Decimal("50")


@dataclass
class LiveState:
    day: str = ""
    start_equity: Decimal = Decimal("0")
    realized_today: Decimal = Decimal("0")
    orders_at_stage: int = 0
    stage: int = 0
    halted: str = ""

    def to_dict(self) -> dict:
        return {"day": self.day, "start_equity": str(self.start_equity),
                "realized_today": str(self.realized_today),
                "orders_at_stage": self.orders_at_stage, "stage": self.stage,
                "halted": self.halted}


class LiveGuard:
    """Shared by every coin's process.

    The cap is an ACCOUNT cap. One guard file per asset would have meant six
    independent $15 caps - $90 a day of real risk while the operator believed
    the number was $15. State therefore lives at the data root and every
    read-modify-write takes an exclusive flock, because six processes racing on
    one JSON file is how a loss counter silently loses increments.
    """

    def __init__(self, path: Path, limits: LiveLimits | None = None) -> None:
        self.path = path
        # Account-wide kill switch, plus a per-coin one for surgical stops.
        self.kill_path = path.parent / "LIVE_KILL"
        self.limits = limits or LiveLimits()
        self.state = LiveState()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _locked(self):
        """Exclusive lock on a sidecar; released when the context exits."""
        import contextlib

        @contextlib.contextmanager
        def ctx():
            lock = self.path.with_suffix(".lock")
            fh = open(lock, "w")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
                fh.close()

        return ctx()

    # ---------------------------------------------------------------- persist
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.exception("live state unreadable; starting fresh")
            return
        self.state = LiveState(
            day=d.get("day", ""),
            start_equity=Decimal(d.get("start_equity", "0")),
            realized_today=Decimal(d.get("realized_today", "0")),
            orders_at_stage=int(d.get("orders_at_stage", 0)),
            stage=int(d.get("stage", 0)),
            halted=d.get("halted", ""),
        )

    def save(self) -> None:
        with self._locked():
            self._save_unlocked()

    def _save_unlocked(self) -> None:
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self.state.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ----------------------------------------------------------------- stages
    @property
    def max_contracts(self) -> int:
        size, _ = STAGES[min(self.state.stage, len(STAGES) - 1)]
        return min(size, self.limits.max_contracts_hard)

    def note_order(self) -> None:
        """Count a placed order and advance the stage when it has earned it."""
        with self._locked():
            self._load()
            self._note_order_unlocked()

    def _note_order_unlocked(self) -> None:
        self.state.orders_at_stage += 1
        _, needed = STAGES[min(self.state.stage, len(STAGES) - 1)]
        if needed and self.state.orders_at_stage >= needed:
            if self.state.stage + 1 < len(STAGES):
                self.state.stage += 1
                self.state.orders_at_stage = 0
                log.warning("live stage -> %d, max contracts now %d",
                            self.state.stage, self.max_contracts)
        self._save_unlocked()

    # ------------------------------------------------------------------ gates
    def roll_day(self, now: datetime, equity: Decimal) -> None:
        today = now.date().isoformat()
        with self._locked():
            self._load()
            if self.state.day != today:
                self.state.day = today
                self.state.start_equity = equity
                self.state.realized_today = Decimal("0")
                self.state.halted = ""
                self._save_unlocked()

    def record_close(self, pnl: Decimal) -> None:
        """Add a realised result to the shared daily counter.

        Read-modify-write under the lock: without it, two coins closing at the
        same instant would each read the old total and one increment would be
        lost - the counter would drift low exactly when losses cluster.
        """
        with self._locked():
            self._load()
            self.state.realized_today += pnl
            self._save_unlocked()

    def blocked(self, equity: Decimal, asset: str | None = None) -> str:
        """Empty string means clear. Any other string is the reason to stop."""
        self._load()                       # another coin may have hit the cap
        if self.kill_path.exists():
            return "LIVE_KILL file present"
        if asset and (self.kill_path.parent / asset / "LIVE_KILL").exists():
            return f"LIVE_KILL for {asset}"
        if equity is None:
            return "equity unknown - refusing to trade blind"
        if equity < self.limits.min_equity_usd:
            return f"equity ${equity} under floor ${self.limits.min_equity_usd}"
        if -self.state.realized_today >= self.limits.daily_loss_usd:
            return (f"daily loss ${-self.state.realized_today} hit cap "
                    f"${self.limits.daily_loss_usd}")
        return ""

    def clamp(self, contracts: Decimal) -> Decimal:
        return min(Decimal(contracts), Decimal(self.max_contracts))


@dataclass
class FillRecord:
    # Latency fields are the certificate: they turn "it did not fill" into a
    # measured cause. book_age_ms is how stale our quote was when we decided;
    # order_ms is the exchange round trip. If rejections correlate with
    # book_age_ms the fix is a faster feed, not a wider limit.
    """One order, and what the market actually did with it.

    fill rate per asset is the entire point of going live: fill_or_kill means
    an unfilled order is the signal, not an error.
    """
    ts: str
    asset: str
    ticker: str
    side: str
    requested: int
    quoted_px: str
    filled: int
    fill_px: str
    status: str
    remaining_sec: float
    p_yes: str
    book_age_ms: float = 0.0
    order_ms: float = 0.0

    @property
    def filled_all(self) -> bool:
        return self.filled >= self.requested and self.requested > 0


class FillLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, rec: FillRecord) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec.__dict__) + "\n")

    def latency_report(self) -> str:
        """Fill rate against book age. The number that decides whether a
        faster feed would help or whether the edge simply is not reachable."""
        try:
            rows = [json.loads(l) for l in
                    self.path.read_text(encoding="utf-8").splitlines() if l.strip()]
        except OSError:
            return "no orders yet"
        if not rows:
            return "no orders yet"
        buckets = {"<100ms": [0, 0], "100-500": [0, 0], "500-1500": [0, 0], ">1500": [0, 0]}
        for r in rows:
            age = float(r.get("book_age_ms") or 0)
            k = ("<100ms" if age < 100 else "100-500" if age < 500
                 else "500-1500" if age < 1500 else ">1500")
            buckets[k][0] += 1
            buckets[k][1] += 1 if int(r.get("filled") or 0) else 0
        out = [f"{'book age':10}{'orders':>8}{'filled':>8}{'rate':>8}"]
        for k, (n, f) in buckets.items():
            if n:
                out.append(f"{k:10}{n:>8}{f:>8}{f/n:>7.0%}")
        lat = [float(r.get("order_ms") or 0) for r in rows if r.get("order_ms")]
        if lat:
            lat.sort()
            out.append(f"\norder round trip: median {lat[len(lat)//2]:.0f}ms  "
                       f"p90 {lat[int(.9*len(lat))]:.0f}ms")
        return "\n".join(out)

    def summary(self) -> str:
        try:
            rows = [json.loads(l) for l in
                    self.path.read_text(encoding="utf-8").splitlines() if l.strip()]
        except OSError:
            return "no live orders yet"
        if not rows:
            return "no live orders yet"
        import collections
        by = collections.defaultdict(lambda: [0, 0, 0])   # orders, filled, contracts
        for r in rows:
            b = by[r["asset"]]
            b[0] += 1
            b[1] += 1 if r["filled"] >= r["requested"] and r["requested"] else 0
            b[2] += int(r["filled"])
        out = [f"{'asset':6}{'orders':>8}{'filled':>8}{'fill rate':>11}{'contracts':>11}"]
        for a, (n, f, c) in sorted(by.items()):
            out.append(f"{a:6}{n:>8}{f:>8}{f/n:>10.0%}{c:>11}")
        return "\n".join(out)
