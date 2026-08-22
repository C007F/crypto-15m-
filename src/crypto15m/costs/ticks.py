from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

ZERO = Decimal("0")
ONE = Decimal("1")
TICK_FINE = Decimal("0.001")
TICK_COARSE = Decimal("0.01")
BAND_LO = Decimal("0.1000")
BAND_HI = Decimal("0.9000")


def tick_size(price: Decimal) -> Decimal:
    if price < BAND_LO or price > BAND_HI:
        return TICK_FINE
    return TICK_COARSE


def _quantize(price: Decimal, tick: Decimal, rounding) -> Decimal:
    steps = (price / tick).to_integral_value(rounding=rounding)
    out = steps * tick
    if out < ZERO:
        return ZERO
    if out > ONE:
        return ONE
    return out


def round_buy_price(price: Decimal) -> Decimal:
    tick = tick_size(price)
    rounded = _quantize(price, tick, ROUND_CEILING)
    tick2 = tick_size(rounded)
    if tick2 != tick:
        rounded = _quantize(price, tick2, ROUND_CEILING)
    return rounded


def round_sell_price(price: Decimal) -> Decimal:
    tick = tick_size(price)
    rounded = _quantize(price, tick, ROUND_FLOOR)
    tick2 = tick_size(rounded)
    if tick2 != tick:
        rounded = _quantize(price, tick2, ROUND_FLOOR)
    return rounded


def yes_sell_price_for_no_buy(no_ask: Decimal) -> Decimal:
    """Buying NO at no_ask is selling YES at 1 - no_ask (Kalshi V2 YES-leg)."""
    return round_sell_price(ONE - no_ask)
