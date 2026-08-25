from __future__ import annotations

import base64
import json
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
ALLOWED_PREFIXES = (
    "/markets",
    "/series/",
    "/portfolio/balance",
    "/portfolio/orders",
    "/portfolio/events/orders",
    "/portfolio/positions",
    "/portfolio/fills",
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
        return live[0]

    def get_market(self, ticker: str) -> MarketQuote:
        return parse_market(self.public_get(f"/markets/{ticker}")["market"])

    # Overridable so livecheck can try the documented path without a code edit.
    # V2. /portfolio/orders is V1 and returns HTTP 410.
    order_path = "/portfolio/events/orders"

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
