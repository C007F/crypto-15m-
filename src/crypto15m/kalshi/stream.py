"""WebSocket book streamer: one process, every coin, push not poll.

Why this exists: the traders polled REST every 2 seconds, so a price could
appear and vanish unseen - HYPE repriced 0.23 -> 0.48 in 13s while we were
blind between polls - and seven pollers sat on the rate limit hard enough to
429 the data they needed. The WS pushes every book change in ~25ms and is not
request-metered.

Architecture: this daemon subscribes to orderbook_delta for the current
window's market on every series, maintains the books in memory, and writes
top-of-book to data/books/<ticker>.json on change. The traders read that file
(a sub-millisecond local read) inside with_book() and fall back to REST only
when the file is stale. One socket replaces ~3.5 req/s of orderbook polling.

The books here are BIDS on each leg, same as the REST orderbook: buying NO
crosses resting YES bids at 1 - best_yes_bid, and YES mirrors it.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from decimal import Decimal
from pathlib import Path

import websockets

from crypto15m.assets import ASSETS
from crypto15m.config import load_settings
from crypto15m.kalshi.client import KalshiClient

log = logging.getLogger("stream")

WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"
BOOKS_DIR = Path(__file__).resolve().parents[3] / "data" / "books"
DISCOVER_EVERY = 20.0        # re-list open markets this often (1 REST call/series)
WRITE_MIN_GAP = 0.15         # per-ticker write throttle, seconds


class Book:
    """One market's resting bids, built from a snapshot plus deltas."""

    __slots__ = ("yes", "no", "seq", "last_write")

    def __init__(self) -> None:
        self.yes: dict[Decimal, Decimal] = {}
        self.no: dict[Decimal, Decimal] = {}
        self.seq = 0
        self.last_write = 0.0

    def snapshot(self, msg: dict) -> None:
        self.yes = {Decimal(str(p)): Decimal(str(sz)) for p, sz in (msg.get("yes") or [])}
        self.no = {Decimal(str(p)): Decimal(str(sz)) for p, sz in (msg.get("no") or [])}

    def delta(self, msg: dict) -> None:
        side = msg.get("side")
        px = Decimal(str(msg.get("price_dollars") or msg.get("price")))
        d = Decimal(str(msg.get("delta_fp") or msg.get("delta") or 0))
        levels = self.yes if side == "yes" else self.no
        cur = levels.get(px, Decimal("0")) + d
        if cur <= 0:
            levels.pop(px, None)
        else:
            levels[px] = cur

    def top(self) -> dict | None:
        if not self.yes or not self.no:
            return None
        by = max(self.yes)
        bn = max(self.no)
        return {
            "yes_ask": str(Decimal("1") - bn), "yes_ask_size": str(self.no[bn]),
            "no_ask": str(Decimal("1") - by), "no_ask_size": str(self.yes[by]),
            "yes_bid": str(by), "no_bid": str(bn),
            "ts": time.time(),
        }


def _ws_headers(client: KalshiClient, settings) -> dict:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    ts = str(int(time.time() * 1000))
    sig = client._load_key().sign(
        f"{ts}GET{WS_PATH}".encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": settings.api_key_id,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
    }


class Streamer:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.client = KalshiClient(self.settings)
        self.books: dict[str, Book] = {}
        self.subscribed: set[str] = set()
        self.sid_to_ticker: dict[int, str] = {}
        BOOKS_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- discovery
    def discover(self) -> set[str]:
        """Current window's market per series. Runs in a thread, REST is fine
        here: 8 calls every 20s is nothing next to the polling it replaces."""
        out: set[str] = set()
        for key, spec in ASSETS.items():
            try:
                ms = self.client.public_get(
                    "/markets", {"series_ticker": spec.series,
                                 "status": "open", "limit": 2})
            except Exception:
                continue
            for m in ms.get("markets") or []:
                out.add(m["ticker"])
        return out

    def write_top(self, ticker: str) -> None:
        book = self.books.get(ticker)
        if book is None:
            return
        now = time.time()
        if now - book.last_write < WRITE_MIN_GAP:
            return
        top = book.top()
        if top is None:
            return
        book.last_write = now
        p = BOOKS_DIR / f"{ticker}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(top))
        tmp.replace(p)

    # ------------------------------------------------------------------ main
    async def run(self) -> None:
        backoff = 1.0
        while True:
            try:
                await self._session()
                backoff = 1.0
            except Exception as exc:
                log.warning("stream dropped: %s - reconnecting in %.0fs",
                            str(exc)[:120], backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _session(self) -> None:
        hdrs = _ws_headers(self.client, self.settings)
        async with websockets.connect(WS_URL, additional_headers=hdrs,
                                      open_timeout=15, ping_interval=10) as ws:
            log.info("connected")
            self.subscribed.clear()
            self.sid_to_ticker.clear()
            next_id = 1
            last_discover = 0.0
            while True:
                now = time.time()
                if now - last_discover > DISCOVER_EVERY:
                    last_discover = now
                    want = await asyncio.to_thread(self.discover)
                    new = want - self.subscribed
                    if new:
                        await ws.send(json.dumps({
                            "id": next_id, "cmd": "subscribe",
                            "params": {"channels": ["orderbook_delta"],
                                       "market_tickers": sorted(new)}}))
                        next_id += 1
                        self.subscribed |= new
                        log.info("subscribed %s", sorted(new))
                    # drop books for closed windows so the dir stays small
                    for t in list(self.books):
                        if t not in want:
                            self.books.pop(t, None)
                            (BOOKS_DIR / f"{t}.json").unlink(missing_ok=True)
                            self.subscribed.discard(t)
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                except asyncio.TimeoutError:
                    continue
                r = json.loads(raw)
                ty = r.get("type")
                msg = r.get("msg") or {}
                ticker = msg.get("market_ticker")
                if ty == "orderbook_snapshot" and ticker:
                    self.books.setdefault(ticker, Book()).snapshot(msg)
                    self.write_top(ticker)
                elif ty == "orderbook_delta" and ticker:
                    b = self.books.setdefault(ticker, Book())
                    b.delta(msg)
                    self.write_top(ticker)
                elif ty == "error":
                    log.warning("ws error: %s", msg)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    asyncio.run(Streamer().run())


if __name__ == "__main__":
    main()
