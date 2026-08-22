from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from crypto15m.experts.tabular import N_FEATURES, sigmoid
from crypto15m.perception.candles import COINBASE


def _candles(session: requests.Session, product: str, start: datetime, end: datetime) -> list[list]:
    r = session.get(
        COINBASE.format(product=product),
        params={
            "granularity": 60,
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
        },
        timeout=15,
    )
    r.raise_for_status()
    rows = r.json() or []
    rows.sort(key=lambda row: row[0])
    return rows


def fetch_history(session: requests.Session, product: str, hours: int = 48) -> list[float]:
    """Paginate Coinbase 1m candles. ~300 bars per request."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    closes: dict[int, float] = {}
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(minutes=280), end)
        try:
            rows = _candles(session, product, cursor, chunk_end)
        except Exception:
            break
        for row in rows:
            if row and len(row) >= 5:
                closes[int(row[0])] = float(row[4])
        cursor = chunk_end
    times = sorted(closes)
    return [closes[t] for t in times]


def _window_features(spot: list[float], ref: list[float], i: int, open_i: int) -> list[float] | None:
    """Features at minute i (decision). Label is window close vs window open — non-overlapping."""
    if i < 20 or open_i < 0 or open_i + 14 >= len(spot):
        return None
    now = spot[i]
    if now <= 0:
        return None

    def ret(arr: list[float], n: int) -> float:
        if i - n < 0 or arr[i - n] <= 0:
            return 0.0
        return arr[i] / arr[i - n] - 1.0

    def vol(arr: list[float], n: int, horizon: int) -> float:
        if i < n + 1:
            return 80.0
        rets = []
        for k in range(i - n, i):
            a, b = arr[k], arr[k + 1]
            if a > 0 and b > 0:
                rets.append(math.log(b / a))
        if len(rets) < 8:
            return 80.0
        m = sum(rets) / len(rets)
        var = sum((x - m) ** 2 for x in rets) / (len(rets) - 1)
        return math.sqrt(var) * math.sqrt(horizon) * 10_000

    open_px = spot[open_i]
    if open_px <= 0:
        return None
    dist = (now - open_px) / open_px * 10_000
    remaining_frac = max(open_i + 14 - i, 1) / 15.0
    z1, z3, z5, z15 = ret(spot, 1), ret(spot, 3), ret(spot, 5), ret(spot, 15)
    acc = z1 - (ret(spot, 2) - ret(spot, 1) if i >= 2 else 0.0)
    b5 = ret(spot, 5) if i < len(spot) else 0.0
    b15 = ret(spot, 15) if i < len(spot) else 0.0
    v15 = vol(spot, 30, 15)
    v5 = vol(spot, 12, 5)
    regime = 1.0 if v5 > v15 * 0.7 else (-1.0 if v5 < v15 * 0.4 else 0.0)
    seq = []
    for n in range(15, 0, -1):
        seq.append(ret(spot, n) - ret(spot, n - 1) if n > 1 else ret(spot, 1))
    core = [
        dist,
        remaining_frac,
        10 / 15,
        z1 * 10_000,
        z3 * 10_000,
        z5 * 10_000,
        z15 * 10_000,
        acc * 10_000,
        b5 * 10_000,
        b15 * 10_000,
        (z5 - b5) * 10_000,
        v15,
        v5,
        regime,
        0.5,  # no Kalshi odds in bootstrap
        0.04,
    ]
    vec = core + seq
    if len(vec) != N_FEATURES:
        return None
    label = 1.0 if spot[open_i + 14] >= open_px else 0.0
    return vec + [label]


def fit_logistic(xs: list[list[float]], ys: list[float], steps: int = 250, lr: float = 0.002) -> tuple[list[float], float]:
    n = len(xs[0])
    w = [0.0] * n
    b = 0.0
    m = len(xs)
    for _ in range(steps):
        gw = [0.0] * n
        gb = 0.0
        for x, y in zip(xs, ys):
            z = b + sum(wj * xj for wj, xj in zip(w, x))
            p = sigmoid(z)
            err = p - y
            gb += err
            for j in range(n):
                gw[j] += err * x[j]
        b -= lr * gb / m
        for j in range(n):
            w[j] -= lr * (gw[j] / m + 1e-4 * w[j])
    return w, b


def save_model(path: Path, weights: list[float], bias: float, n: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"weights": weights, "bias": bias, "n": n, "features": N_FEATURES}), encoding="utf-8")


def train_and_save(path: Path, hours: int = 36, spot_product: str = "BTC-USD",
                   ref_product: str = "ETH-USD") -> dict:
    session = requests.Session()
    spot = fetch_history(session, spot_product, hours=hours)
    ref = fetch_history(session, ref_product, hours=hours)
    n = min(len(spot), len(ref))
    spot, ref = spot[-n:], ref[-n:]
    xs: list[list[float]] = []
    ys: list[float] = []
    # Non-overlapping 15-minute buckets; decide at minute 10 (5m left).
    for open_i in range(20, n - 16, 15):
        i = open_i + 10
        row = _window_features(spot, ref, i, open_i)
        if not row:
            continue
        xs.append(row[:-1])
        ys.append(row[-1])
    if len(xs) < 40:
        return {"ok": False, "n": len(xs)}
    w, b = fit_logistic(xs, ys)
    save_model(path, w, b, len(xs))
    # holdout-ish last 20% accuracy
    cut = int(len(xs) * 0.8)
    acc = 0
    tot = 0
    for x, y in zip(xs[cut:], ys[cut:]):
        p = sigmoid(b + sum(wj * xj for wj, xj in zip(w, x)))
        pred = 1.0 if p >= 0.5 else 0.0
        acc += int(pred == y)
        tot += 1
    return {"ok": True, "n": len(xs), "holdout_acc": (acc / tot if tot else None)}
