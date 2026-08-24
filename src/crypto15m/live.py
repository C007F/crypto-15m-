"""Live-trading guard rails. Nothing here is tunable, ever.

The paper books proved a signal. Going live tests one different question: do
the stale quotes micro finds actually fill? Orders go out fill-or-kill, so a
bad fill is impossible - you either get the quoted price or nothing. That
makes FILL RATE the measurement, not slippage.

Three hard limits and a kill switch. The tuner has no reference to this module.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

log = logging.getLogger(__name__)


# Step-up schedule. Fill rate at size 1 tells you nothing about size 15, so
# size rises only after a stage has produced enough orders to judge.
STAGES: tuple[tuple[int, int], ...] = (
    (1, 50),      # (max_contracts, orders needed before the next stage)
    (3, 50),
    (5, 50),
    (8, 0),       # 0 = terminal; raise the cap deliberately, not automatically
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
    def __init__(self, path: Path, limits: LiveLimits | None = None) -> None:
        self.path = path
        self.kill_path = path.parent / "LIVE_KILL"
        self.limits = limits or LiveLimits()
        self.state = LiveState()
        self._load()

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
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ----------------------------------------------------------------- stages
    @property
    def max_contracts(self) -> int:
        size, _ = STAGES[min(self.state.stage, len(STAGES) - 1)]
        return min(size, self.limits.max_contracts_hard)

    def note_order(self) -> None:
        """Count a placed order and advance the stage when it has earned it."""
        self.state.orders_at_stage += 1
        _, needed = STAGES[min(self.state.stage, len(STAGES) - 1)]
        if needed and self.state.orders_at_stage >= needed:
            if self.state.stage + 1 < len(STAGES):
                self.state.stage += 1
                self.state.orders_at_stage = 0
                log.warning("live stage -> %d, max contracts now %d",
                            self.state.stage, self.max_contracts)
        self.save()

    # ------------------------------------------------------------------ gates
    def roll_day(self, now: datetime, equity: Decimal) -> None:
        today = now.date().isoformat()
        if self.state.day != today:
            self.state.day = today
            self.state.start_equity = equity
            self.state.realized_today = Decimal("0")
            self.state.halted = ""
            self.save()

    def record_close(self, pnl: Decimal) -> None:
        self.state.realized_today += pnl
        self.save()

    def blocked(self, equity: Decimal) -> str:
        """Empty string means clear. Any other string is the reason to stop."""
        if self.kill_path.exists():
            return "LIVE_KILL file present"
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
