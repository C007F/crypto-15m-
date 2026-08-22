from __future__ import annotations

import math
from decimal import Decimal

import requests

from crypto15m.domain import SpotQuote

COINBASE = "https://api.exchange.coinbase.com/products/{product}"


def fetch_spot_quote(product: str = "BTC-USD",
                     session: requests.Session | None = None) -> SpotQuote:
    """Coinbase spot is a proxy for settlement, not the CF Benchmarks RTI."""
    http = session or requests.Session()
    r = http.get(COINBASE.format(product=product) + "/ticker", timeout=10)
    r.raise_for_status()
    data = r.json()
    return SpotQuote(
        bid=Decimal(str(data["bid"])),
        ask=Decimal(str(data["ask"])),
        last=Decimal(str(data["price"])),
        source=f"coinbase-{product.lower()}",
    )


def estimate_vol_bps_15m(product: str = "BTC-USD",
                         session: requests.Session | None = None,
                         fallback: Decimal = Decimal("120")) -> Decimal:
    http = session or requests.Session()
    r = http.get(COINBASE.format(product=product) + "/candles",
                 params={"granularity": 60}, timeout=10)
    r.raise_for_status()
    rows = r.json()
    closes = [float(row[4]) for row in reversed(rows) if row and row[4]]
    if len(closes) < 20:
        return fallback
    rets = []
    for a, b in zip(closes, closes[1:]):
        if a > 0 and b > 0:
            rets.append(math.log(b / a))
    if len(rets) < 15:
        return fallback
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    sigma_15m = math.sqrt(var) * math.sqrt(15)
    bps = Decimal(str(round(sigma_15m * 10_000, 2)))
    if bps < Decimal("20"):
        return fallback
    if bps > Decimal("800"):
        return Decimal("800")
    return bps
