from __future__ import annotations

import math
from dataclasses import dataclass, field

import json
import os
import time
from pathlib import Path

import requests

COINBASE = "https://api.exchange.coinbase.com/products/{product}/candles"


@dataclass
class CandleStore:
    """Ascending 1-minute closes. Coinbase returns newest-first; we sort."""

    product: str
    closes: list[float] = field(default_factory=list)
    times: list[int] = field(default_factory=list)

    def refresh(self, session: requests.Session, limit: int = 300) -> None:
        """Refresh from a cache shared by every trader process.

        Six of the seven traders use BTC-USD as their reference product, so
        they were each pulling identical candles from Coinbase every 15s. That
        - not Kalshi - was the source of the 429s: the failing URL was
        api.exchange.coinbase.com/products/BTC-USD/candles, and a second Kalshi
        account would not have touched it.

        One process fetches per product per interval and writes to disk; the
        rest read the file. A slightly stale candle set is harmless - these are
        1-minute bars - whereas a rate-limited one is empty.
        """
        rows = self._cached_rows()
        if rows is None:
            try:
                r = session.get(COINBASE.format(product=self.product),
                                params={"granularity": 60}, timeout=10)
                r.raise_for_status()
                rows = r.json() or []
                self._write_cache(rows)
            except Exception:
                # Rate-limited or down. A stale candle set of ANY age beats an
                # empty one - these are 1-minute bars and the alternative is no
                # spot context at all.
                rows = self._cached_rows(max_age=None)
                if rows is None:
                    raise
        rows = [row for row in rows if row and len(row) >= 5]
        rows.sort(key=lambda row: row[0])
        if limit:
            rows = rows[-limit:]
        self.times = [int(row[0]) for row in rows]
        self.closes = [float(row[4]) for row in rows]

    # --- shared cache -------------------------------------------------------
    CACHE_DIR = Path("/home/cam/projects/crypto-15m-bot/data/candles")
    CACHE_TTL = 20.0          # a touch above the 15s refresh interval

    def _cache_path(self) -> Path:
        return self.CACHE_DIR / f"{self.product}.json"

    def _cached_rows(self, max_age: float | None = -1.0):
        """max_age None = accept any age (fallback path); -1 = use the TTL."""
        try:
            p = self._cache_path()
            ttl = self.CACHE_TTL if max_age == -1.0 else max_age
            if ttl is not None and time.time() - p.stat().st_mtime > ttl:
                return None
            return json.loads(p.read_text())
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def _write_cache(self, rows) -> None:
        try:
            self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
            p = self._cache_path()
            tmp = p.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(rows))
            tmp.replace(p)              # atomic; last writer wins, all equivalent
        except OSError:
            pass

    def ret(self, minutes: int) -> float:
        if len(self.closes) < minutes + 1:
            return 0.0
        a = self.closes[-(minutes + 1)]
        b = self.closes[-1]
        if a <= 0:
            return 0.0
        return b / a - 1.0

    def log_rets(self, n: int) -> list[float]:
        if len(self.closes) < n + 1:
            return []
        out = []
        window = self.closes[-(n + 1) :]
        for a, b in zip(window, window[1:]):
            if a > 0 and b > 0:
                out.append(math.log(b / a))
        return out

    def vol_bps(self, minutes: int, horizon: int = 15) -> float:
        rets = self.log_rets(minutes)
        if len(rets) < 8:
            return 0.0
        mean = sum(rets) / len(rets)
        var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
        return math.sqrt(var) * math.sqrt(horizon) * 10_000

    def last_n_rets(self, n: int) -> tuple[float, ...]:
        rets = []
        if len(self.closes) < n + 1:
            return tuple(0.0 for _ in range(n))
        for i in range(n, 0, -1):
            a = self.closes[-i - 1]
            b = self.closes[-i]
            rets.append((b / a - 1.0) if a > 0 else 0.0)
        return tuple(rets)

    def accel(self) -> float:
        return self.ret(1) - (self.closes[-2] / self.closes[-3] - 1.0 if len(self.closes) >= 3 and self.closes[-3] > 0 else 0.0)
