from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from crypto15m.domain import MarketQuote, SpotQuote
from crypto15m.perception.candles import CandleStore


def distance_bps(spot: Decimal, strike: Decimal) -> Decimal:
    if strike <= 0:
        return Decimal("0")
    return (spot - strike) / strike * Decimal("10000")


def _d(x: float) -> Decimal:
    return Decimal(str(round(x, 6)))


@dataclass
class FeatureFrame:
    asof: datetime
    market: MarketQuote
    spot: SpotQuote
    vol_bps_15m: Decimal
    remaining_sec: float
    opened_sec: float
    distance_bps: Decimal
    spot_ret_1m: Decimal = Decimal("0")
    spot_ret_3m: Decimal = Decimal("0")
    spot_ret_5m: Decimal = Decimal("0")
    spot_ret_15m: Decimal = Decimal("0")
    spot_accel: Decimal = Decimal("0")
    ref_ret_5m: Decimal = Decimal("0")
    ref_ret_15m: Decimal = Decimal("0")
    spot_ref_5m: Decimal = Decimal("0")
    vol_bps_5m: Decimal = Decimal("0")
    vol_regime: Decimal = Decimal("0")  # -1 low, 0 mid, +1 high
    yes_mid: Decimal = Decimal("0")
    implied_p_yes: Decimal = Decimal("0.5")
    spread: Decimal = Decimal("0")
    seq_1m: tuple[float, ...] = field(default_factory=tuple)

    def as_vector(self) -> list[float]:
        """Tabular + last-15 1m returns (sequence flattened)."""
        core = [
            float(self.distance_bps),
            self.remaining_sec / 900.0,
            self.opened_sec / 900.0,
            float(self.spot_ret_1m) * 10_000,
            float(self.spot_ret_3m) * 10_000,
            float(self.spot_ret_5m) * 10_000,
            float(self.spot_ret_15m) * 10_000,
            float(self.spot_accel) * 10_000,
            float(self.ref_ret_5m) * 10_000,
            float(self.ref_ret_15m) * 10_000,
            float(self.spot_ref_5m) * 10_000,
            float(self.vol_bps_15m),
            float(self.vol_bps_5m),
            float(self.vol_regime),
            float(self.implied_p_yes),
            float(self.spread),
        ]
        seq = list(self.seq_1m) if self.seq_1m else [0.0] * 15
        if len(seq) < 15:
            seq = [0.0] * (15 - len(seq)) + seq
        return core + seq[:15]


def build_frame(
    *,
    asof: datetime,
    market: MarketQuote,
    spot: SpotQuote,
    spot_candles: CandleStore | None,
    ref_candles: CandleStore | None,
    fallback_vol: Decimal,
) -> FeatureFrame:
    remaining = (market.close_time - asof).total_seconds()
    opened = (asof - market.open_time).total_seconds()
    dist = distance_bps(spot.mid, market.strike)
    vol15 = fallback_vol
    vol5 = Decimal("0")
    regime = Decimal("0")
    z1 = z3 = z5 = z15 = acc = Decimal("0")
    b5 = b15 = zb = Decimal("0")
    seq: tuple[float, ...] = tuple()
    if spot_candles and len(spot_candles.closes) >= 20:
        z1 = _d(spot_candles.ret(1))
        z3 = _d(spot_candles.ret(3))
        z5 = _d(spot_candles.ret(5))
        z15 = _d(spot_candles.ret(15))
        acc = _d(spot_candles.accel())
        v15 = spot_candles.vol_bps(30, 15)
        v5 = spot_candles.vol_bps(12, 5)
        if v15 > 0:
            vol15 = Decimal(str(round(v15, 2)))
        if v5 > 0:
            vol5 = Decimal(str(round(v5, 2)))
        if v15 > 0 and v5 > 0:
            ratio = v5 / max(v15 * math_sqrt_5_over_15(), 1e-6)
            if ratio > 1.25:
                regime = Decimal("1")
            elif ratio < 0.75:
                regime = Decimal("-1")
        seq = spot_candles.last_n_rets(15)
    if ref_candles and len(ref_candles.closes) >= 20:
        b5 = _d(ref_candles.ret(5))
        b15 = _d(ref_candles.ret(15))
        zb = z5 - b5
    yes_mid = Decimal("0")
    if market.yes_bid > 0 and market.yes_ask > 0:
        yes_mid = (market.yes_bid + market.yes_ask) / Decimal("2")
    implied = yes_mid if yes_mid > 0 else Decimal("0.5")
    return FeatureFrame(
        asof=asof,
        market=market,
        spot=spot,
        vol_bps_15m=vol15,
        remaining_sec=remaining,
        opened_sec=opened,
        distance_bps=dist,
        spot_ret_1m=z1,
        spot_ret_3m=z3,
        spot_ret_5m=z5,
        spot_ret_15m=z15,
        spot_accel=acc,
        ref_ret_5m=b5,
        ref_ret_15m=b15,
        spot_ref_5m=zb,
        vol_bps_5m=vol5,
        vol_regime=regime,
        yes_mid=yes_mid,
        implied_p_yes=implied,
        spread=market.spread,
        seq_1m=seq,
    )


def math_sqrt_5_over_15() -> float:
    return (5 / 15) ** 0.5
