from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path

from crypto15m.domain import ExpertView
from crypto15m.perception.features import FeatureFrame

N_FEATURES = 16 + 15


def sigmoid(z: float) -> float:
    if z < -30:
        return 0.0
    if z > 30:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


class TabularExpert:
    """Logistic on tabular+sequence features. Loads data/models/logistic.json if present."""

    name = "tabular"

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.weights: list[float] | None = None
        self.bias = 0.0
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        w = data.get("weights") or []
        if len(w) != N_FEATURES:
            return
        self.weights = [float(x) for x in w]
        self.bias = float(data.get("bias") or 0.0)

    def predict(self, features: FeatureFrame) -> ExpertView:
        if self.weights is None:
            return ExpertView(
                name=self.name,
                p_yes=Decimal("0.5"),
                magnitude_bps=Decimal("0"),
                uncertainty=Decimal("0.9"),
            )
        x = features.as_vector()
        z = self.bias + sum(wi * xi for wi, xi in zip(self.weights, x))
        p = min(max(sigmoid(z), 0.02), 0.98)
        # Bootstrap is Coinbase 15m, not CF Benchmarks. Shrink and stay uncertain.
        p = 0.5 + (p - 0.5) * 0.4
        unc = 1.0 / (1.0 + abs(z))
        unc = min(max(unc, 0.45), 0.9)
        return ExpertView(
            name=self.name,
            p_yes=Decimal(str(round(p, 4))),
            magnitude_bps=features.distance_bps.copy_abs(),
            uncertainty=Decimal(str(round(unc, 4))),
        )
