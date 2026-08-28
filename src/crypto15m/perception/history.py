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
import json
import logging
import math
import os
import time
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)

COINBASE = "https://api.exchange.coinbase.com/products/{product}/candles"
HORIZONS_MIN = (1, 2, 3, 5, 8, 10, 12, 15)

ROOT = Path(__file__).resolve().parents[3]
MAX_AGE = 86400.0                       # rebuild daily; also the cache's TTL
# Board Memo 006 part 2. built_at only advances on SUCCESS, so before this a
# failing build was retried on every tick - 0.5s at current POLL_SEC - and
# seven traders restarting together turned one Coinbase outage into 14,710
# tracebacks and a self-sustaining 429 storm (7,431 in the 04:00Z hour of
# 8/28 alone). The ladder is per-process; jitter keeps the herd from
# resynchronising on it.
BACKOFF_LADDER = (60.0, 300.0, 900.0)


class EmpiricalMoves:
    """Distribution of absolute moves (bps) per horizon, from 1m closes.

    Board Memo 006 part 1: the distribution is persisted to disk. It used to
    live only in memory, so every restart - and this system restarts often -
    threw away five days of measurement and refetched ~25 paginated Coinbase
    pages per asset while trading continued on the Gaussian fallback that this
    module exists to replace.
    """

    # Shared across processes like CandleStore's, though in practice each
    # trader owns a distinct spot product; the point is surviving restarts.
    CACHE_DIR = ROOT / "data" / "history"

    def __init__(self, product: str, days: int = 5) -> None:
        self.product = product
        self.days = days
        self.moves: dict[int, list[float]] = {}
        self.built_at = 0.0
        self._fails = 0
        self._next_try = 0.0
        # Stable per-product offset: the ladder must not re-synchronise the
        # herd after a shared outage. Seeded from the name, not hash(), which
        # Python randomises per process.
        self._jitter = 1.0 + ((zlib.crc32(product.encode()) % 400) - 200) / 1000.0

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
        """Load from disk if fresh, else refetch. Backs off on failure."""
        if self.load_cached():
            return True
        try:
            closes = self._fetch_closes(session)
        except Exception:
            self._note_failure()
            log.exception("history fetch failed for %s (retry in %.0fs)",
                          self.product, max(0.0, self._next_try - time.time()))
            return False
        if len(closes) < 500:
            self._note_failure()
            log.warning("only %d candles for %s; not enough (retry in %.0fs)",
                        len(closes), self.product,
                        max(0.0, self._next_try - time.time()))
            return False
        self._set_moves(closes)
        self._fails = 0
        self._next_try = 0.0
        self._write_cache()
        log.info("%s: empirical moves from %d candles (%.1f days)",
                 self.product, len(closes), len(closes) / 1440)
        return True

    def _set_moves(self, closes: list[float]) -> None:
        for h in HORIZONS_MIN:
            mv = []
            for i in range(h, len(closes)):
                a, b = closes[i - h], closes[i]
                if a > 0:
                    mv.append(abs(10_000.0 * (b - a) / a))
            mv.sort()
            self.moves[h] = mv
        self.built_at = time.time()

    def _note_failure(self) -> None:
        step = BACKOFF_LADDER[min(self._fails, len(BACKOFF_LADDER) - 1)]
        self._fails += 1
        self._next_try = time.time() + step * self._jitter

    @property
    def stale(self) -> bool:
        return (time.time() - self.built_at) > MAX_AGE      # rebuild daily

    @property
    def should_build(self) -> bool:
        """Stale AND past the backoff window. This is what callers must ask.

        `stale` still answers only "is the data old", so a caller that polls
        every 0.5s does not turn a Coinbase outage into a retry storm.
        """
        return self.stale and time.time() >= self._next_try

    # ------------------------------------------------------------ persistence
    def _cache_path(self) -> Path:
        return self.CACHE_DIR / f"{self.product}.json"

    def load_cached(self, max_age: float = MAX_AGE) -> bool:
        """Adopt a cached distribution if one is present and fresh enough."""
        try:
            raw = json.loads(self._cache_path().read_text())
            built_at = float(raw["built_at"])
            if time.time() - built_at > max_age:
                return False
            moves = {int(h): [float(x) for x in v] for h, v in raw["moves"].items()}
        except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError):
            return False
        # A truncated or malformed file must not read as a usable brain.
        if set(moves) != set(HORIZONS_MIN) or any(len(v) < 500 for v in moves.values()):
            return False
        for v in moves.values():
            v.sort()                    # p_move_exceeds bisects; never trust file order
        self.moves = moves
        self.built_at = built_at
        self._fails = 0
        self._next_try = 0.0
        return True

    def _write_cache(self) -> None:
        try:
            self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
            p = self._cache_path()
            tmp = p.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps({
                "product": self.product,
                "days": self.days,
                "built_at": self.built_at,
                "moves": {str(h): v for h, v in self.moves.items()},
            }))
            tmp.replace(p)              # atomic; last writer wins, all equivalent
        except OSError:
            log.warning("could not persist history for %s", self.product)

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
