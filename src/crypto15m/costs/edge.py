from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from crypto15m.domain import EdgeReport, MarketQuote, Side
from crypto15m.costs.fees import taker_fee
from crypto15m.costs.ticks import round_buy_price, tick_size


# Cushion in DOLLARS, not ticks. Kalshi's tick is 0.001 below $0.10 and 0.01
# in the middle band, so "3 ticks" meant a 0.3 cent cushion on a 9 cent
# contract and 3 cents on a 43 cent one - a 10x difference in exactly the
# place it matters. Live, every cheap-contract order was crossing by a third
# of a cent and the market moved past it before the order landed.
#
# An absolute cushion also does the right thing economically: 3 cents on a 9
# cent contract is a third of the stake, so expected_value() - which prices
# the trade at this crossed limit - simply refuses those. Cheap contracts you
# cannot chase without destroying the edge get filtered out for free.
# 5 cents, measured. A 3c cushion filled 0/5 live; a 5c cushion filled 2/2 on
# the same books minutes apart, including a cheap contract (bid 0.16 against a
# 0.11 ask, filled at 0.12 - the market moved a cent and the cushion absorbed
# it). The cushion is a LIMIT, not a price paid: fill_or_kill executes at the
# best available level, so a wider cushion costs nothing when the book is
# still and buys a fill when it is not.
CROSS_CUSHION = Decimal("0.05")


def fill_price(ask: Decimal, slippage_ticks: int,
               cushion: Decimal | None = None) -> Decimal:
    """Conservative take: lift the ask by a fixed cushion, then round up.

    slippage_ticks is kept for callers that still pass it; it now scales the
    cushion rather than counting ticks, so 0 means no cushion at all.
    """
    if cushion is None:
        cushion = CROSS_CUSHION
    steps = max(int(slippage_ticks), 0)
    if steps == 0:
        return round_buy_price(ask)
    px = ask + cushion * (Decimal(steps) / Decimal("3"))
    if px >= Decimal("1"):
        px = Decimal("1")
    return round_buy_price(px)


def expected_value(
    *,
    p_yes: Decimal,
    side: Side,
    ask: Decimal,
    contracts: Decimal,
    fee_multiplier: Decimal = Decimal("1"),
    slippage_ticks: int = 1,
) -> tuple[Decimal, Decimal, Decimal]:
    """Return (ev_total, fill_px, fee).

    Binary: win pays $1, lose pays $0. EV is after taker fee on the fill price.
    """
    px = fill_price(ask, slippage_ticks)
    if px >= 1 or px <= 0 or contracts <= 0:
        return Decimal("0"), px, Decimal("0")
    fee = taker_fee(contracts, px, fee_multiplier)
    if side == "yes":
        ev_per = p_yes * (Decimal("1") - px) - (Decimal("1") - p_yes) * px
    else:
        p_no = Decimal("1") - p_yes
        ev_per = p_no * (Decimal("1") - px) - (Decimal("1") - p_no) * px
    return ev_per * contracts - fee, px, fee


def pick_side(
    *,
    p_yes: Decimal,
    uncertainty: Decimal,
    market: MarketQuote,
    min_edge: Decimal,
    uncertainty_k: Decimal,
    max_contracts: Decimal,
    slippage_ticks: int = 1,
) -> EdgeReport:
    """Choose Yes or No only if EV/contract clears fees + uncertainty buffer."""
    buffer = min_edge + uncertainty_k * uncertainty
    none = EdgeReport(
        side=None,
        p_yes=p_yes,
        ask=Decimal("0"),
        fill_px=Decimal("0"),
        fee=Decimal("0"),
        ev=Decimal("0"),
        ev_per_contract=Decimal("0"),
        buffer=buffer,
        contracts=Decimal("0"),
        reason="no qualifying side",
    )
    if market.yes_ask <= 0 or market.no_ask <= 0:
        return EdgeReport(**{**none.__dict__, "reason": "missing ask"})

    best: EdgeReport | None = None
    for side, ask, size in (
        ("yes", market.yes_ask, market.yes_ask_size),
        ("no", market.no_ask, market.no_ask_size),
    ):
        contracts = max_contracts
        if size > 0:
            contracts = min(contracts, size)
        contracts = contracts.to_integral_value(rounding=ROUND_DOWN)
        if contracts < 1:
            continue
        ev, px, fee = expected_value(
            p_yes=p_yes,
            side=side,  # type: ignore[arg-type]
            ask=ask,
            contracts=contracts,
            fee_multiplier=market.fee_multiplier,
            slippage_ticks=slippage_ticks,
        )
        ev_pc = ev / contracts
        if ev_pc < buffer:
            continue
        cand = EdgeReport(
            side=side,  # type: ignore[arg-type]
            p_yes=p_yes,
            ask=ask,
            fill_px=px,
            fee=fee,
            ev=ev,
            ev_per_contract=ev_pc,
            buffer=buffer,
            contracts=contracts,
            reason=f"{side} EV/contract {ev_pc:.4f} >= buffer {buffer:.4f}",
        )
        if best is None or cand.ev > best.ev:
            best = cand
    return best or none
