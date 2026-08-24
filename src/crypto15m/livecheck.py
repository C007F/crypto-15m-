"""One real order, end to end, with everything printed.

The live order path has never been exercised against Kalshi. The body shape,
the endpoint, the price units and the response fields are all assumptions
until a real order proves them. This places exactly ONE contract on the
cheapest available side, prints the raw response, and closes it if it filled.

Nothing else is allowed to touch live trading until this passes.
"""
from __future__ import annotations

import json
from decimal import Decimal

import uuid
from datetime import datetime, timedelta, timezone

from crypto15m.bot import Mind
from crypto15m.config import Settings
from crypto15m.domain import Permit
from crypto15m.kalshi.client import parse_fill


def _permit(ticker: str, side: str, price: Decimal) -> Permit:
    """A real single-use permit for the verification order.

    place_order refuses a live order without one, which is correct and stays
    that way. The check builds a genuine permit rather than bypassing the
    guard, so this exercises the same path the bot will.
    """
    return Permit(
        id=str(uuid.uuid4()), ticker=ticker, side=side,
        max_contracts=Decimal("1"), max_price=price,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        reason="livecheck: single-contract verification",
    )


def run(settings: Settings) -> int:
    mind = Mind(settings, live=True)
    client = mind.client

    print("=" * 66)
    print(f"LIVE CHECK  asset={settings.asset.key}  series={settings.asset.series}")
    print("=" * 66)

    try:
        bal = client.get_balance()
    except Exception as exc:
        print(f"FAIL  balance: {type(exc).__name__}: {exc}")
        return 1
    print(f"balance raw     : {json.dumps(bal)[:200]}")
    cents = bal.get("balance")
    print(f"balance parsed  : ${Decimal(str(cents)) / 100:.2f}  "
          f"(assuming cents; sanity-check this)")

    market = client.get_open_market()
    if market is None:
        print("FAIL  no open market right now; try again inside a window")
        return 1
    print(f"market          : {market.ticker}  "
          f"yes {market.yes_bid}/{market.yes_ask}  no {market.no_bid}/{market.no_ask}")

    # cheapest side, so a 1-contract test risks the least
    side = "yes" if market.yes_ask <= market.no_ask else "no"
    price = market.yes_ask if side == "yes" else market.no_ask
    if not (Decimal("0") < price < Decimal("1")):
        print(f"FAIL  unusable ask {price}")
        return 1
    print(f"\nplacing 1 contract {side.upper()} @ {price}  (max risk ${price})")

    # side is bid|ask on the YES leg: buying NO is selling YES.
    leg = "bid" if side == "yes" else "ask"
    resp = client.place_order(ticker=market.ticker, side=side, count=Decimal("1"),
                              price=price, permit=_permit(market.ticker, side, price),
                              dry_run=False, yes_leg=leg)
    print(f"\nraw response    : {json.dumps(resp, default=str)[:600]}")

    if (resp or {}).get("error"):
        print(f"\nFAIL  {resp['error']}")
        return 1

    filled, avg = parse_fill(resp)
    print(f"\norder_id        : {resp.get('order_id')}")
    print(f"fill_count      : {filled}")
    print(f"avg fill price  : {avg}")
    print(f"remaining_count : {resp.get('remaining_count')}")

    if filled < 1:
        print("\nPASS (no fill). The order path works and fill_or_kill behaved:")
        print("     the quote was gone. Nothing to unwind.")
        return 0

    print("\nfilled - closing it back out")
    # opposite leg to close
    close_leg = "ask" if side == "yes" else "bid"
    close_px = market.yes_bid if side == "yes" else market.yes_ask
    close = client.place_order(ticker=market.ticker, side=side, count=Decimal("1"),
                               price=close_px,
                               permit=_permit(market.ticker, side, close_px),
                               dry_run=False, yes_leg=close_leg)
    print(f"close response  : {json.dumps(close, default=str)[:400]}")
    closed, _ = parse_fill(close)
    if closed < 1:
        print("\nWARNING  the close did NOT fill. You are holding 1 contract.")
        print("         Close it by hand on Kalshi before running anything else.")
        return 1
    print("\nPASS  open and close both worked. The live path is verified.")
    return 0
