from __future__ import annotations

import math
from decimal import Decimal

from crypto15m.domain import ExpertView
from crypto15m.perception.features import FeatureFrame


def _norm_cdf(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-1.702 * z))


class BaselineExpert:
    """Remaining-vol P(close >= strike). Settlement is a 60s average, so last-minute is noisier than this admits."""

    name = "baseline"

    def predict(self, features: FeatureFrame) -> ExpertView:
        remaining = max(features.remaining_sec, 1.0)
        sigma = float(features.vol_bps_15m) * math.sqrt(remaining / 900.0)
        sigma = max(sigma, 1.0)
        z = float(features.distance_bps) / sigma
        p = min(max(_norm_cdf(z), 0.001), 0.999)   # 0.99 ceiling froze the logit once dist >> vol
        unc = min(max(remaining / 900.0, 0.12), 0.95)
        return ExpertView(
            name=self.name,
            p_yes=Decimal(str(round(p, 4))),
            magnitude_bps=features.distance_bps.copy_abs(),
            uncertainty=Decimal(str(round(unc, 4))),
        )
