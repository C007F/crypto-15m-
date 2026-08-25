from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from crypto15m.domain import MarketQuote

# Kept for back-compat; the live series comes from settings.asset.series.
SERIES = "KXBTC15M"


def _dec(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    return Decimal(str(value))


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def parse_market(raw: dict) -> MarketQuote:
    return MarketQuote(
        ticker=raw["ticker"],
        event_ticker=raw.get("event_ticker") or "",
        strike=_dec(raw.get("floor_strike")),
        yes_bid=_dec(raw.get("yes_bid_dollars")),
        yes_ask=_dec(raw.get("yes_ask_dollars")),
        no_bid=_dec(raw.get("no_bid_dollars")),
        no_ask=_dec(raw.get("no_ask_dollars")),
        yes_ask_size=_dec(raw.get("yes_ask_size_fp")),
        no_ask_size=_dec(raw.get("no_ask_size_fp") or raw.get("no_bid_size_fp") or raw.get("yes_bid_size_fp")),
        open_time=parse_ts(raw.get("open_time")) or datetime.now(timezone.utc),
        close_time=parse_ts(raw.get("close_time")) or datetime.now(timezone.utc),
        status=raw.get("status") or "",
        volume=_dec(raw.get("volume_fp")),
        open_interest=_dec(raw.get("open_interest_fp")),
        result=(raw.get("result") or "").lower(),
        exchange_index=int(raw.get("exchange_index") or 0),
        fee_multiplier=_dec(raw.get("fee_multiplier"), "1"),
    )
