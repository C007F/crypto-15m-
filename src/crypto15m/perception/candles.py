from __future__ import annotations

import math
from dataclasses import dataclass, field

import requests

COINBASE = "https://api.exchange.coinbase.com/products/{product}/candles"


@dataclass
class CandleStore:
    """Ascending 1-minute closes. Coinbase returns newest-first; we sort."""

    product: str
    closes: list[float] = field(default_factory=list)
    times: list[int] = field(default_factory=list)

    def refresh(self, session: requests.Session, limit: int = 300) -> None:
        r = session.get(COINBASE.format(product=self.product), params={"granularity": 60}, timeout=10)
        r.raise_for_status()
        rows = r.json() or []
        rows = [row for row in rows if row and len(row) >= 5]
        rows.sort(key=lambda row: row[0])
        if limit:
            rows = rows[-limit:]
        self.times = [int(row[0]) for row in rows]
        self.closes = [float(row[4]) for row in rows]

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
