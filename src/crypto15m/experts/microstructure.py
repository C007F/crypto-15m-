from __future__ import annotations

import math
from decimal import Decimal

from crypto15m.domain import ExpertView
from crypto15m.perception.features import FeatureFrame


def _norm_cdf(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-1.702 * z))


class MicrostructureExpert:
    """If spot has moved and Kalshi Yes mid has not, the book is stale."""

    name = "microstructure"

    def predict(self, features: FeatureFrame) -> ExpertView:
        remaining = max(features.remaining_sec, 1.0)
        sigma = float(features.vol_bps_15m) * math.sqrt(remaining / 900.0)
        sigma = max(sigma, 1.0)
        fair = _norm_cdf(float(features.distance_bps) / sigma)
        implied = float(features.implied_p_yes) if features.implied_p_yes > 0 else 0.5
        lag = fair - implied
        # Push probability toward the spot-implied fair when the book lags.
        p = implied + 0.65 * lag
        p = min(max(p, 0.001), 0.999)
        spread = float(features.spread)
        unc = 0.2 + spread * 2.0 + (0.35 if abs(lag) < 0.04 else 0.05)
        unc = min(max(unc, 0.08), 0.95)
        return ExpertView(
            name=self.name,
            p_yes=Decimal(str(round(p, 4))),
            magnitude_bps=features.distance_bps.copy_abs(),
            uncertainty=Decimal(str(round(unc, 4))),
        )
