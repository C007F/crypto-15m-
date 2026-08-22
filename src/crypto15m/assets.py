"""Per-coin configuration. One codebase, one trader process per asset.

Each asset names its Kalshi series, its Coinbase spot product, a macro
reference product for the regime expert, and a fallback 15m volatility in bps
used only when the candle feed is unavailable. The fallbacks below are
measured, not guessed - see `scripts/measure_vol.py`.

Reference is BTC for every alt, because BTC drives the complex. BTC itself
has no larger crypto driver, so it references ETH and reads the spread as
lead/lag.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Asset:
    key: str
    label: str
    series: str
    spot_product: str
    ref_product: str
    vol_bps: str
    port: int          # dashboard port for this asset's trader


ASSETS: dict[str, Asset] = {
    "btc":  Asset("btc",  "BTC",  "KXBTC15M",  "BTC-USD",  "ETH-USD", "25",  8801),
    "eth":  Asset("eth",  "ETH",  "KXETH15M",  "ETH-USD",  "BTC-USD", "35",  8802),
    "xrp":  Asset("xrp",  "XRP",  "KXXRP15M",  "XRP-USD",  "BTC-USD", "110", 8803),
    "doge": Asset("doge", "DOGE", "KXDOGE15M", "DOGE-USD", "BTC-USD", "80",  8804),
    "sol":  Asset("sol",  "SOL",  "KXSOL15M",  "SOL-USD",  "BTC-USD", "55",  8805),
    "zec":  Asset("zec",  "ZEC",  "KXZEC15M",  "ZEC-USD",  "BTC-USD", "115", 8806),
}


def get(key: str) -> Asset:
    k = (key or "btc").strip().lower()
    if k not in ASSETS:
        raise SystemExit(f"unknown asset {k!r}; known: {', '.join(sorted(ASSETS))}")
    return ASSETS[k]
