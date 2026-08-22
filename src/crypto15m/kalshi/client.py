from __future__ import annotations

import base64
import json
import time
import uuid
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import requests

from crypto15m.config import Settings
from crypto15m.domain import MarketQuote, Permit
from crypto15m.kalshi.markets import parse_market


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
        return f"{self.settings.rest_base}{path}"

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
        data = self.public_get("/markets", params={"series_ticker": self.settings.asset.series,
                                                   "status": "open", "limit": 20})
        markets = [parse_market(m) for m in data.get("markets") or []]
        live = [m for m in markets if m.status in {"open", "active"}]
        if not live:
            return None
        live.sort(key=lambda m: m.close_time)
        return live[0]

    def get_market(self, ticker: str) -> MarketQuote:
        return parse_market(self.public_get(f"/markets/{ticker}")["market"])

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
    ) -> dict:
        if not dry_run and permit is None:
            raise RuntimeError("refusing live order without RiskGovernor permit")
        body = {
            "ticker": ticker,
            "side": yes_leg,
            "count": f"{count:.2f}",
            "price": f"{price:.4f}",
            "time_in_force": "fill_or_kill",
            "self_trade_prevention_type": "taker_at_cross",
            "client_order_id": str(uuid.uuid4()),
        }
        if dry_run:
            return {"dry_run": True, "request": body, "status": "simulated", "permit_id": permit.id if permit else None}
        code, payload = self.auth_post("/portfolio/events/orders", body)
        payload["http_status"] = code
        payload["request"] = body
        return payload
