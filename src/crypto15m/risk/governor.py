from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from crypto15m.domain import Denial, HaltReason, Intent, Permit, PortfolioSnapshot

NY = ZoneInfo("America/New_York")


def ny_date(now: datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(NY).date().isoformat()


@dataclass(frozen=True)
class RiskLimits:
    daily_loss_pct: Decimal = Decimal("0.025")
    daily_loss_usd: Decimal = Decimal("25")
    per_trade_risk_usd: Decimal = Decimal("10")
    max_exposure_usd: Decimal = Decimal("50")
    max_trades_per_day: int = 16
    max_drawdown_pct: Decimal = Decimal("0.08")
    max_contracts_per_trade: Decimal = Decimal("5")
    max_spread: Decimal = Decimal("0.08")
    one_position_per_window: bool = True
    permit_ttl_sec: int = 8


class RiskGovernor:
    """Hard safety layer. Nothing below this module may bypass allow().

    Daily loss cap uses NY calendar date. Drawdown kill persists in `kill_path`
    until an operator deletes the file.
    """

    def __init__(
        self,
        limits: RiskLimits,
        *,
        state_path: Path | None = None,
        kill_path: Path | None = None,
    ) -> None:
        self.limits = limits
        self.state_path = state_path
        self.kill_path = kill_path
        self.halt = HaltReason.NONE
        self._consumed: set[str] = set()
        self._ny_date: str | None = None
        self._load()

    def effective_daily_cap(self, day_start_equity: Decimal) -> Decimal:
        pct = (day_start_equity * self.limits.daily_loss_pct).quantize(Decimal("0.01"))
        usd = self.limits.daily_loss_usd
        if usd is not None and usd > 0:
            return min(pct, usd) if pct > 0 else usd
        return pct

    def is_halted(self) -> bool:
        return self.halt is not HaltReason.NONE

    def must_flatten(self) -> bool:
        return self.halt in {HaltReason.DAILY_LOSS_CAP, HaltReason.MAX_DRAWDOWN, HaltReason.MANUAL}

    def observe(self, snap: PortfolioSnapshot) -> HaltReason:
        """Update halt from portfolio. Called before every allow()."""
        if self._ny_date is not None and snap.ny_date != self._ny_date:
            self.roll_day(snap)
        self._ny_date = snap.ny_date
        if self.kill_path and self.kill_path.exists():
            self.halt = HaltReason.MAX_DRAWDOWN
            self._save(snap)
            return self.halt

        cap = self.effective_daily_cap(snap.day_start_equity)
        if snap.realized_pnl_today <= -cap or (snap.equity - snap.day_start_equity) <= -cap:
            self.halt = HaltReason.DAILY_LOSS_CAP
            self._save(snap)
            return self.halt

        if snap.high_water > 0:
            dd = (snap.high_water - snap.equity) / snap.high_water
            if dd >= self.limits.max_drawdown_pct:
                self.halt = HaltReason.MAX_DRAWDOWN
                if self.kill_path:
                    self.kill_path.write_text(
                        f"drawdown kill {dd:.4f} equity={snap.equity} hw={snap.high_water}\n",
                        encoding="utf-8",
                    )
                self._save(snap)
                return self.halt

        if snap.trades_today >= self.limits.max_trades_per_day:
            self.halt = HaltReason.MAX_TRADES
            self._save(snap)
            return self.halt

        if self.halt is HaltReason.MAX_TRADES and snap.trades_today < self.limits.max_trades_per_day:
            self.halt = HaltReason.NONE
        if self.halt is HaltReason.DAILY_LOSS_CAP:
            # only a new NY date (via snapshot.ny_date change in persist) may clear
            pass
        self._save(snap)
        return self.halt

    def roll_day(self, snap: PortfolioSnapshot) -> None:
        """New NY date: clear daily halt and trade count; keep drawdown kill."""
        if self.halt is HaltReason.MAX_DRAWDOWN or (self.kill_path and self.kill_path.exists()):
            self.halt = HaltReason.MAX_DRAWDOWN
        else:
            self.halt = HaltReason.NONE
        self._save(snap)

    def allow(self, intent: Intent, snap: PortfolioSnapshot) -> Permit | Denial:
        self.observe(snap)
        if self.halt is HaltReason.MAX_DRAWDOWN:
            return Denial("drawdown kill switch", self.halt)
        if self.halt is HaltReason.DAILY_LOSS_CAP:
            return Denial("daily loss cap — no new trades until next NY day", self.halt)
        if self.halt is HaltReason.MANUAL:
            return Denial("manual halt", self.halt)
        if self.halt is HaltReason.MAX_TRADES:
            return Denial("max trades per day", self.halt)

        if intent.spread > self.limits.max_spread:
            return Denial(f"spread {intent.spread} > max {self.limits.max_spread}")
        if intent.contracts < 1:
            return Denial("contracts < 1")
        if intent.limit_price <= 0 or intent.limit_price >= 1:
            return Denial("limit price out of (0,1)")

        contracts = min(intent.contracts, self.limits.max_contracts_per_trade)
        premium = contracts * intent.limit_price
        if premium > self.limits.per_trade_risk_usd:
            shrink = (self.limits.per_trade_risk_usd / intent.limit_price).to_integral_value()
            if shrink < 1:
                return Denial("per-trade risk cap: cannot afford 1 contract")
            contracts = shrink
            premium = contracts * intent.limit_price

        remaining_cap = self.effective_daily_cap(snap.day_start_equity) + snap.realized_pnl_today
        if remaining_cap <= 0:
            self.halt = HaltReason.DAILY_LOSS_CAP
            return Denial("daily loss cap exhausted", self.halt)
        if premium > remaining_cap:
            shrink = (remaining_cap / intent.limit_price).to_integral_value()
            if shrink < 1:
                return Denial("daily remaining cap < 1 contract")
            contracts = min(contracts, shrink)
            premium = contracts * intent.limit_price

        if snap.open_exposure + premium > self.limits.max_exposure_usd:
            room = self.limits.max_exposure_usd - snap.open_exposure
            if room <= 0:
                return Denial("max exposure")
            shrink = (room / intent.limit_price).to_integral_value()
            if shrink < 1:
                return Denial("max exposure: cannot fit 1 contract")
            contracts = min(contracts, shrink)

        if self.limits.one_position_per_window and intent.window_ticker in snap.tickers:
            return Denial("already in this window")

        if snap.trades_today + 1 > self.limits.max_trades_per_day:
            return Denial("max trades per day", HaltReason.MAX_TRADES)

        now = snap.asof
        permit = Permit(
            id=str(uuid.uuid4()),
            ticker=intent.ticker,
            side=intent.side,
            max_contracts=contracts,
            max_price=intent.limit_price,
            expires_at=now + timedelta(seconds=self.limits.permit_ttl_sec),
            reason=f"ok remaining_cap={remaining_cap} size={contracts}",
        )
        return permit

    def consume(self, permit: Permit, now: datetime) -> Denial | None:
        if permit.id in self._consumed:
            return Denial("permit already used")
        if now > permit.expires_at:
            return Denial("permit expired")
        self._consumed.add(permit.id)
        return None

    def _load(self) -> None:
        if self.kill_path and self.kill_path.exists():
            self.halt = HaltReason.MAX_DRAWDOWN
        if not self.state_path or not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        reason = data.get("halt") or "none"
        try:
            self.halt = HaltReason(reason)
        except ValueError:
            self.halt = HaltReason.NONE
        self._ny_date = data.get("ny_date")

    def _save(self, snap: PortfolioSnapshot | None) -> None:
        if not self.state_path:
            return
        payload = {
            "halt": self.halt.value,
            "ny_date": snap.ny_date if snap else None,
            "day_start_equity": str(snap.day_start_equity) if snap else None,
            "high_water": str(snap.high_water) if snap else None,
            "trades_today": snap.trades_today if snap else None,
            "realized_pnl_today": str(snap.realized_pnl_today) if snap else None,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
