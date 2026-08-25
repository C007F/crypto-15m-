"""Empirical move distributions from historical candles.

The baseline and microstructure experts price P(close >= strike) with a
normal CDF over realized vol. Crypto moves have fat tails, so the Gaussian
systematically underprices large moves and overprices certainty near the
strike - the model once said 0.93 on an event that was ~0.99.

This replaces the assumption with measurement: pull days of 1-minute candles
once, build the actual distribution of |move| per horizon, and answer
"how often has a move this big happened in this much time" by lookup.
More data, zero new parameters.
"""
from __future__ import annotations

import bisect
import logging
import math
import time
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

COINBASE = "https://api.exchange.coinbase.com/products/{product}/candles"
HORIZONS_MIN = (1, 2, 3, 5, 8, 10, 12, 15)


class EmpiricalMoves:
    """Distribution of absolute moves (bps) per horizon, from 1m closes."""

    def __init__(self, product: str, days: int = 5) -> None:
        self.product = product
        self.days = days
        self.moves: dict[int, list[float]] = {}
        self.built_at = 0.0

    # ------------------------------------------------------------------ build
    def _fetch_closes(self, session: requests.Session | None = None) -> list[float]:
        http = session or requests.Session()
        closes: list[float] = []
        end = datetime.now(timezone.utc)
        # Coinbase caps 300 candles per request; page backwards.
        for _ in range(self.days * 5):
            start = end - timedelta(minutes=300)
            r = http.get(COINBASE.format(product=self.product),
                         params={"granularity": 60,
                                 "start": start.isoformat(),
                                 "end": end.isoformat()},
                         timeout=15)
            r.raise_for_status()
            rows = r.json()
            if not rows:
                break
            closes = [float(x[4]) for x in reversed(rows) if x and x[4]] + closes
            end = start
            time.sleep(0.15)                    # stay friendly to the public API
            if len(closes) >= self.days * 1440:
                break
        return closes

    def build(self, session: requests.Session | None = None) -> bool:
        try:
            closes = self._fetch_closes(session)
        except Exception:
            log.exception("history fetch failed for %s", self.product)
            return False
        if len(closes) < 500:
            log.warning("only %d candles for %s; not enough", len(closes), self.product)
            return False
        for h in HORIZONS_MIN:
            mv = []
            for i in range(h, len(closes)):
                a, b = closes[i - h], closes[i]
                if a > 0:
                    mv.append(abs(10_000.0 * (b - a) / a))
            mv.sort()
            self.moves[h] = mv
        self.built_at = time.time()
        log.info("%s: empirical moves from %d candles (%.1f days)",
                 self.product, len(closes), len(closes) / 1440)
        return True

    @property
    def stale(self) -> bool:
        return (time.time() - self.built_at) > 86400        # rebuild daily

    # ----------------------------------------------------------------- lookup
    def p_move_exceeds(self, dist_bps: float, remaining_sec: float) -> float | None:
        """P(|move| >= dist) within remaining_sec, by historical frequency."""
        if not self.moves:
            return None
        mins = max(1.0, remaining_sec / 60.0)
        # nearest horizon at or above, scaled by sqrt-time from the closest
        h = min(HORIZONS_MIN, key=lambda x: abs(x - mins))
        mv = self.moves.get(h)
        if not mv:
            return None
        scale = math.sqrt(mins / h)
        d = abs(dist_bps) / max(scale, 1e-9)
        i = bisect.bisect_left(mv, d)
        # survival with a floor: never claim an event is impossible just
        # because the sample did not contain it
        n = len(mv)
        return max((n - i) / n, 0.5 / n)

    def p_yes(self, dist_bps: float, remaining_sec: float) -> float | None:
        """P(close >= strike) given signed distance and time left.

        Symmetric-move assumption: P(cross) = p_exceed of the gap, halved for
        direction. Above the strike, YES needs the move NOT to cross down.
        """
        p_cross = self.p_move_exceeds(dist_bps, remaining_sec)
        if p_cross is None:
            return None
        if dist_bps >= 0:
            return 1.0 - 0.5 * p_cross
        return 0.5 * p_cross
