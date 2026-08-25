from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

Side = Literal["yes", "no"]


class HaltReason(str, Enum):
    NONE = "none"
    DAILY_LOSS_CAP = "daily_loss_cap"
    MAX_DRAWDOWN = "max_drawdown"
    MAX_TRADES = "max_trades"
    MANUAL = "manual"


@dataclass(frozen=True)
class MarketQuote:
    ticker: str
    event_ticker: str
    strike: Decimal
    yes_bid: Decimal
    yes_ask: Decimal
    no_bid: Decimal
    no_ask: Decimal
    yes_ask_size: Decimal
    no_ask_size: Decimal
    open_time: datetime
    close_time: datetime
    status: str
    volume: Decimal
    open_interest: Decimal
    result: str
    # Which Kalshi exchange the market lives on. Orders omitting it default to
    # 0, and the 15m crypto series moved to 2 on 2026-08-25.
    exchange_index: int = 0
    fee_multiplier: Decimal = Decimal("1")

    @property
    def spread(self) -> Decimal:
        if self.yes_ask <= 0 or self.yes_bid <= 0:
            return Decimal("1")
        return self.yes_ask - self.yes_bid


@dataclass(frozen=True)
class SpotQuote:
    bid: Decimal
    ask: Decimal
    last: Decimal
    source: str

    @property
    def mid(self) -> Decimal:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / Decimal("2")
        return self.last


@dataclass(frozen=True)
class ExpertView:
    name: str
    p_yes: Decimal
    magnitude_bps: Decimal
    uncertainty: Decimal  # 0 = sure, 1 = no idea


@dataclass(frozen=True)
class EdgeReport:
    side: Side | None
    p_yes: Decimal
    ask: Decimal
    fill_px: Decimal
    fee: Decimal
    ev: Decimal
    ev_per_contract: Decimal
    buffer: Decimal
    contracts: Decimal
    reason: str


@dataclass(frozen=True)
class Intent:
    """Desired trade before risk. Never sent to the exchange directly."""

    ticker: str
    side: Side
    contracts: Decimal
    limit_price: Decimal  # price we pay for the chosen side
    spread: Decimal
    ev: Decimal
    p_yes: Decimal
    reason: str
    window_ticker: str


@dataclass(frozen=True)
class Permit:
    """Single-use ticket from RiskGovernor. Executor must present this."""

    id: str
    ticker: str
    side: Side
    max_contracts: Decimal
    max_price: Decimal
    expires_at: datetime
    reason: str


@dataclass(frozen=True)
class Denial:
    reason: str
    halt: HaltReason = HaltReason.NONE


@dataclass
class Position:
    ticker: str
    side: Side
    contracts: Decimal
    avg_price: Decimal
    fees: Decimal


@dataclass
class Fill:
    ts: datetime
    ticker: str
    side: Side
    contracts: Decimal
    price: Decimal
    fee: Decimal
    kind: str  # open | flatten | settle
    permit_id: str | None = None


@dataclass
class PortfolioSnapshot:
    asof: datetime
    ny_date: str
    cash: Decimal
    equity: Decimal
    day_start_equity: Decimal
    high_water: Decimal
    realized_pnl_today: Decimal
    open_exposure: Decimal
    open_positions: int
    trades_today: int
    tickers: tuple[str, ...] = field(default_factory=tuple)
