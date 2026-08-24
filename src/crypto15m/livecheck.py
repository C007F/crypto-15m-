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

from crypto15m.bot import Mind
from crypto15m.config import Settings


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

    resp = client.place_order(ticker=market.ticker, side=side, count=Decimal("1"),
                              price=price, permit=None, dry_run=False,
                              yes_leg=side, action="buy")
    print(f"\nraw response    : {json.dumps(resp, default=str)[:600]}")

    order = (resp or {}).get("order") or {}
    if not order:
        print("\nFAIL  no `order` object in the response.")
        print("      The parser expects resp['order']['filled_count'] and")
        print("      resp['order']['average_fill_price']. Fix trials._fill to")
        print("      match the shape above before running anything live.")
        return 1

    filled = int(float(order.get("filled_count") or 0))
    print(f"\nfilled_count    : {filled}")
    print(f"avg fill price  : {order.get('average_fill_price')}")
    print(f"status          : {order.get('status')}")

    if filled < 1:
        print("\nPASS (no fill). The order path works and fill_or_kill behaved:")
        print("     the quote was gone. Nothing to unwind.")
        return 0

    print("\nfilled - closing it back out")
    close = client.place_order(ticker=market.ticker, side=side, count=Decimal("1"),
                               price=(market.yes_bid if side == "yes" else market.no_bid),
                               permit=None, dry_run=False, yes_leg=side, action="sell")
    print(f"close response  : {json.dumps(close, default=str)[:400]}")
    c_order = (close or {}).get("order") or {}
    if int(float(c_order.get("filled_count") or 0)) < 1:
        print("\nWARNING  the close did NOT fill. You are holding 1 contract.")
        print("         Close it by hand on Kalshi before running anything else.")
        return 1
    print("\nPASS  open and close both worked. The live path is verified.")
    return 0
