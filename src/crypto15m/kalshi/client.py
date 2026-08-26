from __future__ import annotations

from dataclasses import replace
import base64
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import requests

from crypto15m.config import Settings
from crypto15m.domain import MarketQuote, Permit
from crypto15m.kalshi.markets import parse_market


# Every path this client may ever touch. Deposits, withdrawals and transfers
# are absent by construction, and _guard_path() raises on anything not listed,
# so a future edit cannot quietly add one. The bot can read markets, read its
# balance, and place orders. It cannot move money.
log = logging.getLogger(__name__)

ALLOWED_PREFIXES = (
    "/markets",
    "/series/",
    "/portfolio/balance",
    "/portfolio/orders",
    "/portfolio/events/orders",
    "/portfolio/positions",
    "/portfolio/fills",
    # WebSocket upgrade path. Read-only market-data subscription - the WS API
    # has no order or money-movement commands on these channels, and the
    # FORBIDDEN word check still applies to it like everything else.
    "/trade-api/ws",
    "/portfolio/settlements",   # read-only history; what the Kalshi UI shows
)

FORBIDDEN = ("deposit", "withdraw", "transfer", "payment", "bank", "ach", "wallet")


def parse_fill(resp: dict) -> tuple[int, Decimal | None]:
    """(contracts filled, average fill price) from a V2 order response.

    V2 returns fill_count and average_fill_price at the TOP LEVEL - there is
    no `order` wrapper, and the field is fill_count not filled_count. Reading
    the wrong shape would have made every order look unfilled, which reads as
    a dead strategy rather than a parsing bug.
    """
    if not resp:
        return 0, None
    body = resp.get("order") if isinstance(resp.get("order"), dict) else resp
    raw = body.get("fill_count", body.get("filled_count"))
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        n = 0
    px = body.get("average_fill_price")
    if px in (None, "", 0):
        return n, None
    try:
        val = Decimal(str(px))
    except Exception:
        return n, None
    # dollars come back as a fixed-point string; cents as an integer > 1
    return n, (val / Decimal("100") if val > 1 else val)


def _guard_path(path: str) -> str:
    low = path.lower()
    for word in FORBIDDEN:
        if word in low:
            raise RuntimeError(f"refusing to call a money-movement endpoint: {path}")
    if not any(low.startswith(p) for p in ALLOWED_PREFIXES):
        raise RuntimeError(f"endpoint not on the allowlist: {path}")
    return path


class KalshiClient:
    """Kalshi Trade API v2. Same RSA-PSS headers as kalshi_python_sync.

    Official wheel requires CPython 3.13; Chester is 3.12. Live orders still
    require a RiskGovernor Permit — this client will refuse without one.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "crypto-15m-mind/0.3"})
        self._private_key = None

    def _url(self, path: str) -> str:
        return f"{self.settings.rest_base}{_guard_path(path)}"

    def _load_key(self):
        if self._private_key is not None:
            return self._private_key
        from cryptography.hazmat.primitives import serialization

        key_path: Path = self.settings.private_key_path
        if not key_path.exists():
            raise FileNotFoundError(f"Kalshi private key not found: {key_path}")
        with key_path.open("rb") as fh:
            self._private_key = serialization.load_pem_private_key(fh.read(), password=None)
        return self._private_key

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        if not self.settings.api_key_id:
            raise RuntimeError("KALSHI_API_KEY_ID is empty")
        ts = str(int(time.time() * 1000))
        sign_path = urlparse(self._url(path)).path
        message = f"{ts}{method.upper()}{sign_path}".encode("utf-8")
        key = self._load_key()
        signature = key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.settings.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
        }

    def public_get(self, path: str, params: dict | None = None) -> dict:
        r = self.session.get(self._url(path), params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def auth_get(self, path: str, params: dict | None = None) -> dict:
        headers = self._auth_headers("GET", path)
        r = self.session.get(self._url(path), params=params, headers=headers, timeout=15)
        r.raise_for_status()
        return r.json()

    def auth_post(self, path: str, body: dict) -> tuple[int, dict]:
        headers = self._auth_headers("POST", path)
        headers["Content-Type"] = "application/json"
        r = self.session.post(self._url(path), headers=headers, data=json.dumps(body), timeout=15)
        try:
            payload = r.json()
        except ValueError:
            payload = {"raw": r.text}
        return r.status_code, payload

    def get_series(self) -> dict:
        return self.public_get(f"/series/{self.settings.asset.series}")["series"]

    def get_open_market(self) -> MarketQuote | None:
        """Current 15m contract.

        The window ticker only rotates every 15 minutes, so listing 20 markets
        every tick was fetching ~20x the payload needed and was the call that
        drew 429s once several coins ran at once. Once the ticker is known we
        poll it directly and only re-list when it expires.
        """
        cached = getattr(self, "_open_ticker", None)
        if cached:
            try:
                m = self.get_market(cached)
                if m.status in {"open", "active"} and m.close_time > datetime.now(timezone.utc):
                    return m
            except Exception:
                pass
            self._open_ticker = None

        data = self.public_get("/markets", params={"series_ticker": self.settings.asset.series,
                                                   "status": "open", "limit": 20})
        markets = [parse_market(m) for m in data.get("markets") or []]
        live = [m for m in markets if m.status in {"open", "active"}]
        if not live:
            return None
        live.sort(key=lambda m: m.close_time)
        self._open_ticker = live[0].ticker
        return self.with_book(live[0])

    _ob_cache: dict = {}

    def orderbook_top(self, ticker: str) -> tuple | None:
        # Short TTL cache. Adding an orderbook read to every tick doubled this
        # process's request rate, and 7 traders at POLL_SEC=2 sat on Kalshi's
        # ceiling. The book does not meaningfully change inside one poll, so a
        # sub-poll cache is free accuracy-wise and halves the calls.
        _hit = self._ob_cache.get(ticker)
        if _hit and (time.time() - _hit[0]) < 1.6:
            return _hit[1]
        out = self._orderbook_top_uncached(ticker)
        self._ob_cache[ticker] = (time.time(), out)
        return out

    def _orderbook_top_uncached(self, ticker: str) -> tuple | None:
        """Best crossable ask on each side, from the resting book.

        Kalshi's orderbook is bids-only on both legs. Buying NO fills against
        resting YES bids, so the ask we actually have to pay is 1 - best_yes_bid
        and the depth available is that level's size; buying YES is the mirror.

        This exists because the /markets summary quotes drift from the book -
        measured no_ask 0.68 against a true 0.71 on SOL - and a NO limit built
        off the stale number lands a cent under the real ask, so the order is
        accepted and fills nothing. 32 of 35 post-IOC orders died that way.

        Returns (yes_ask, yes_ask_size, no_ask, no_ask_size) or None if the
        book is unreadable, in which case the caller keeps the summary quote.
        """
        try:
            ob = self.public_get(f"/markets/{ticker}/orderbook",
                                 params={"depth": 5}).get("orderbook_fp") or {}
        except Exception as exc:
            # A swallowed failure here is dangerous: with_book() falls back to
            # the /markets summary quote, which is the exact pricing that got
            # 32 of 35 orders accepted and filled nothing. Under rate limiting
            # that would silently revert the fix. Mark it so callers can refuse
            # to trade rather than trade blind.
            self.book_stale = True
            log.warning("orderbook read failed for %s (%s) - "
                        "pricing would fall back to stale quotes",
                        ticker, str(exc)[:80])
            return None
        yes_bids = ob.get("yes_dollars") or []
        no_bids = ob.get("no_dollars") or []
        if not yes_bids or not no_bids:
            return None
        # Levels come back ascending; the best bid is the highest price.
        by = max((Decimal(str(px)), Decimal(str(sz))) for px, sz in yes_bids)
        bn = max((Decimal(str(px)), Decimal(str(sz))) for px, sz in no_bids)
        return {"yes_ask": Decimal("1") - bn[0], "yes_ask_size": bn[1],
                "no_ask": Decimal("1") - by[0], "no_ask_size": by[1],
                "yes_bid": by[0], "no_bid": bn[0]}

    # data/books/<ticker>.json, maintained by crypto15m-stream (kalshi/stream.py).
    # A fresh streamed top is better than a REST read: ~25ms old at worst,
    # costs zero requests, and cannot 429.
    BOOKS_DIR = Path(__file__).resolve().parents[3] / "data" / "books"
    STREAM_FRESH_SEC = 3.0

    def _streamed_top(self, ticker: str) -> dict | None:
        try:
            d = json.loads((self.BOOKS_DIR / f"{ticker}.json").read_text())
            if time.time() - float(d["ts"]) > self.STREAM_FRESH_SEC:
                return None
            return {k: Decimal(d[k]) for k in
                    ("yes_ask", "yes_ask_size", "no_ask", "no_ask_size",
                     "yes_bid", "no_bid")}
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            return None

    def with_book(self, q: MarketQuote) -> MarketQuote:
        """Overlay true book prices/sizes onto a summary quote.

        Prefers the WebSocket-streamed book; falls back to a REST orderbook
        read; sets `book_stale` when neither worked so the entry gate declines
        rather than pricing off the summary quote.
        """
        self.book_stale = False
        top = self._streamed_top(q.ticker)
        if top is None:
            top = self.orderbook_top(q.ticker)
        if top is None:
            return q
        if not (0 < top["yes_ask"] < 1) or not (0 < top["no_ask"] < 1):
            return q
        # Replace BOTH sides. Overlaying only the asks left yes_bid from the
        # stale summary, so spread = yes_ask - yes_bid mixed two clocks and
        # read ~13c on a 1c book. uncertainty = 0.2 + spread*2 then doubled,
        # the edge buffer swallowed every signal, and the bot went silent for
        # two hours while perfectly healthy.
        return replace(q, **top)

    def get_market(self, ticker: str) -> MarketQuote:
        return self.with_book(parse_market(self.public_get(f"/markets/{ticker}")["market"]))

    # Overridable so livecheck can try the documented path without a code edit.
    # V2. /portfolio/orders is V1 and returns HTTP 410.
    order_path = "/portfolio/events/orders"

    # True when the last with_book() could not read the real orderbook.
    book_stale = False

    def get_balance(self) -> dict:
        return self.auth_get("/portfolio/balance")

    def place_order(
        self,
        *,
        ticker: str,
        side: str,
        count: Decimal,
        price: Decimal,
        permit: Permit | None,
        dry_run: bool,
        yes_leg: str,
        action: str = "buy",
        market_exchange_index: int | None = None,
    ) -> dict:
        if not dry_run and permit is None:
            raise RuntimeError("refusing live order without RiskGovernor permit")
        # V2 order shape, verified against a live 410 and Kalshi's docs.
        # `side` is bid|ask on the YES leg, NOT yes|no: buying NO is expressed
        # as selling YES. yes_leg_price() already returns it that way.
        # count and price are fixed-point STRINGS, not numbers.
        body = {
            "ticker": ticker,
            "side": yes_leg,
            "count": f"{Decimal(count):.2f}",
            "price": f"{Decimal(price):.4f}",
            # immediate_or_cancel, not fill_or_kill. Conviction sizing asks for
            # up to 8 contracts while depth is routinely 2 - SOL and DOGE both
            # showed 2 resting - and all-or-nothing threw the whole order away
            # rather than taking what was there. A partial fill is the same
            # edge on fewer contracts, which is strictly better than none, and
            # the accounting already sizes from the filled count.
            "time_in_force": "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross",
            "client_order_id": str(uuid.uuid4()),
        }
        # Kalshi runs multiple exchanges. The 15m crypto series moved from
        # exchange 0 to exchange 2 overnight on 2026-08-25; orders that omit
        # the index default to 0 and come back market_not_found even though
        # the market reads fine. Send the market's own index.
        xi = getattr(market_exchange_index, "value", market_exchange_index)
        if xi is not None:
            body["exchange_index"] = int(xi)
        if dry_run:
            return {"dry_run": True, "request": body, "status": "simulated", "permit_id": permit.id if permit else None}
        code, payload = self.auth_post(self.order_path, body)
        payload["http_status"] = code
        payload["request"] = body
        return payload
