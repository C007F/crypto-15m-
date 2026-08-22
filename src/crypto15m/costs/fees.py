from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

CENT = Decimal("0.01")
BASE_MULTIPLIER = Decimal("0.07")


def taker_fee(contracts: Decimal, price: Decimal, fee_multiplier: Decimal = Decimal("1")) -> Decimal:
    """Kalshi quadratic taker: ceil(0.07 * M * C * P * (1-P)) to the next cent.

    Peaks at 50¢. Recheck the live fee schedule before going live.
    """
    if contracts <= 0 or price <= 0:
        return Decimal("0.00")
    raw = BASE_MULTIPLIER * fee_multiplier * contracts * price * (Decimal("1") - price)
    if raw <= 0:
        return Decimal("0.00")
    return raw.quantize(CENT, rounding=ROUND_CEILING)


def fee_per_contract(price: Decimal, fee_multiplier: Decimal = Decimal("1")) -> Decimal:
    return taker_fee(Decimal("1"), price, fee_multiplier)
