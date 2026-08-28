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
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import websockets

from crypto15m.assets import ASSETS
from crypto15m.config import load_settings
from crypto15m.kalshi.client import KalshiClient

log = logging.getLogger("stream")

WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"
BOOKS_DIR = Path(__file__).resolve().parents[3] / "data" / "books"
HEARTBEAT_PATH = BOOKS_DIR / "heartbeat"
SHADOW_PATH = BOOKS_DIR.parent / "calendar_shadow.jsonl"
DISCOVER_EVERY = 20.0        # re-list open markets this often (1 REST call/series)
WRITE_MIN_GAP = 0.15         # per-ticker write throttle, seconds
ET = ZoneInfo("America/New_York")

# Board Memo 005 part 2. Synthesized tickers are LOGGED against discover()
# for 24h before they may drive a subscribe. Flip only after the chairman
# reviews the shadow log.
SHADOW_CALENDAR = True


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


def series_of(ticker: str) -> str:
    return ticker.split("-", 1)[0]


def tickers_to_drop(held: set[str], want: set[str], ok_series: set[str]) -> set[str]:
    """Board Memo 005 part 1.

    Only drop a ticker whose series listed successfully and which is
    genuinely gone. A 429ed series is absence of evidence, not evidence of
    absence — keep its books.
    """
    drop: set[str] = set()
    for t in held:
        if series_of(t) not in ok_series:
            continue
        if t not in want:
            drop.add(t)
    return drop


def synthesize_window_tickers(now: datetime | None = None) -> dict[str, str]:
    """Current-window ticker per series, from the clock. No API.

    Kalshi 15m crypto tickers are `{series}-{YY}{MON}{DD}{HHMM}-{MM}` where
    HHMM is the window CLOSE in America/New_York and MM is that close's
    minutes (00/15/30/45). Example: KXBTC15M-26AUG281215-15.
    """
    now_et = (now or datetime.now(timezone.utc)).astimezone(ET)
    close_min = ((now_et.minute // 15) + 1) * 15
    close = now_et.replace(second=0, microsecond=0)
    if close_min >= 60:
        close = close.replace(minute=0) + timedelta(hours=1)
    else:
        close = close.replace(minute=close_min)
    token = f"{close.strftime('%y')}{close.strftime('%b').upper()}{close.strftime('%d')}{close.strftime('%H%M')}-{close.strftime('%M')}"
    return {spec.series: f"{spec.series}-{token}" for spec in ASSETS.values()}


def write_heartbeat(now: float | None = None) -> None:
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = HEARTBEAT_PATH.with_suffix(".tmp")
    tmp.write_text(str(now if now is not None else time.time()))
    tmp.replace(HEARTBEAT_PATH)


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
    def discover(self) -> tuple[set[str], set[str]]:
        """Current window's market per series.

        Returns (tickers found, series that listed without error). A 429ed
        series is omitted from the second set so cleanup will not treat it
        as closed (Memo 005 part 1).
        """
        found: set[str] = set()
        ok_series: set[str] = set()
        for key, spec in ASSETS.items():
            try:
                ms = self.client.public_get(
                    "/markets", {"series_ticker": spec.series,
                                 "status": "open", "limit": 2})
            except Exception as exc:
                log.warning("discover failed for %s: %s", spec.series, str(exc)[:80])
                continue
            ok_series.add(spec.series)
            for m in ms.get("markets") or []:
                ticker = m.get("ticker")
                if ticker:
                    found.add(ticker)
        return found, ok_series

    def _record_calendar_shadow(self, want: set[str], ok_series: set[str]) -> None:
        """Memo 005 part 2: log synthesized tickers against discover().

        Does not subscribe. After 24h the chairman can flip SHADOW_CALENDAR
        off and let synthesis drive the subscribe path.
        """
        synth = synthesize_window_tickers()
        synth_set = set(synth.values())
        missing = sorted(t for t in synth_set if t not in want)
        extra = sorted(t for t in want if t not in synth_set)
        row = {
            "ts": time.time(),
            "synth": synth,
            "discovered": sorted(want),
            "ok_series": sorted(ok_series),
            "missing": missing,
            "extra": extra,
        }
        try:
            with SHADOW_PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except OSError as exc:
            log.warning("calendar shadow write failed: %s", exc)
        if missing or extra:
            log.info("calendar shadow mismatch missing=%s extra=%s", missing, extra)

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

    def _drop(self, ticker: str) -> None:
        self.books.pop(ticker, None)
        (BOOKS_DIR / f"{ticker}.json").unlink(missing_ok=True)
        self.subscribed.discard(ticker)

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
                write_heartbeat()
                now = time.time()
                if now - last_discover > DISCOVER_EVERY:
                    last_discover = now
                    want, ok_series = await asyncio.to_thread(self.discover)
                    if SHADOW_CALENDAR:
                        self._record_calendar_shadow(want, ok_series)
                    # Subscribe path uses discover() only. Synthesis is
                    # shadow until the chairman flips it. Memo 005 part 2.
                    new = want - self.subscribed
                    if new:
                        await ws.send(json.dumps({
                            "id": next_id, "cmd": "subscribe",
                            "params": {"channels": ["orderbook_delta"],
                                       "market_tickers": sorted(new)}}))
                        next_id += 1
                        self.subscribed |= new
                        log.info("subscribed %s", sorted(new))
                    held = set(self.books) | set(self.subscribed)
                    for t in tickers_to_drop(held, want, ok_series):
                        self._drop(t)
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                except asyncio.TimeoutError:
                    # Heartbeat quiet books. A book that has not CHANGED is
                    # still perfectly current, but a file that has not been
                    # WRITTEN looks stale to the traders, whose freshness
                    # window (3s) then sends them to REST every tick - at 3am
                    # the quiet books (zec, near) had seven traders hammering
                    # Kalshi for data this process already held, 429ing the
                    # observer's discovery calls into starvation.
                    now2 = time.time()
                    for t, b in list(self.books.items()):
                        if now2 - b.last_write > 1.5:
                            self.write_top(t)
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
