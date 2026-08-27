"""Coinbase spot streamer: one WS, every product, spot on disk.

Stage A of the speed work. Each trader currently REST-polls its spot price
every tick, which is the second of the two calls pinning the poll interval at
2s. This daemon subscribes to Coinbase's ticker channel for every product in
ASSETS and writes data/spot/<PRODUCT>.json on each tick, so a trader can read
spot in microseconds. Runs beside the traders; nothing reads these files until
Stage B flips them over, so this cannot affect live trading today.

Same shape as the Kalshi book streamer (kalshi/stream.py): reconnect with
backoff, heartbeat-stamp quiet products, one socket total.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import websockets

from crypto15m.assets import ASSETS

log = logging.getLogger("spot-stream")

WS_URL = "wss://ws-feed.exchange.coinbase.com"
SPOT_DIR = Path(__file__).resolve().parents[3] / "data" / "spot"
WRITE_MIN_GAP = 0.15


class SpotStreamer:
    def __init__(self) -> None:
        self.products = sorted({a.spot_product for a in ASSETS.values()}
                               | {a.ref_product for a in ASSETS.values()})
        self.last: dict[str, dict] = {}
        self.last_write: dict[str, float] = {}
        SPOT_DIR.mkdir(parents=True, exist_ok=True)

    def write(self, product: str, data: dict, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_write.get(product, 0) < WRITE_MIN_GAP:
            return
        self.last_write[product] = now
        p = SPOT_DIR / f"{product}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(p)

    async def run(self) -> None:
        backoff = 1.0
        while True:
            try:
                await self._session()
                backoff = 1.0
            except Exception as exc:
                log.warning("spot stream dropped: %s - retry in %.0fs",
                            str(exc)[:120], backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _session(self) -> None:
        async with websockets.connect(WS_URL, open_timeout=15,
                                      ping_interval=15) as ws:
            await ws.send(json.dumps({
                "type": "subscribe",
                "channels": [{"name": "ticker", "product_ids": self.products}]}))
            log.info("subscribed %s", self.products)
            last_hb = time.time()
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
                except asyncio.TimeoutError:
                    # heartbeat: a quiet product's last price is still current,
                    # but only re-stamp while the FEED is alive (we got here via
                    # timeout, so check the socket is not long-dead overall)
                    if time.time() - last_hb > 30:
                        raise RuntimeError("no ticker messages for 30s")
                    for prod, d in list(self.last.items()):
                        d["ts"] = time.time()
                        self.write(prod, d, force=True)
                    continue
                m = json.loads(raw)
                if m.get("type") != "ticker":
                    continue
                last_hb = time.time()
                prod = m.get("product_id")
                try:
                    d = {"price": float(m["price"]),
                         "bid": float(m.get("best_bid") or m["price"]),
                         "ask": float(m.get("best_ask") or m["price"]),
                         "ts": time.time()}
                except (KeyError, TypeError, ValueError):
                    continue
                self.last[prod] = d
                self.write(prod, d)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    asyncio.run(SpotStreamer().run())


if __name__ == "__main__":
    main()
