from __future__ import annotations

from decimal import Decimal

from crypto15m.domain import ExpertView
from crypto15m.perception.features import FeatureFrame


class MomentumExpert:
    """Short-horizon BTC tape. Aligned 1/3/5m returns raise conviction."""

    name = "momentum"

    def predict(self, features: FeatureFrame) -> ExpertView:
        r1 = float(features.spot_ret_1m)
        r3 = float(features.spot_ret_3m)
        r5 = float(features.spot_ret_5m)
        acc = float(features.spot_accel)
        votes = 0.0
        votes += 1.0 if r1 > 0 else (-1.0 if r1 < 0 else 0.0)
        votes += 1.0 if r3 > 0 else (-1.0 if r3 < 0 else 0.0)
        votes += 1.0 if r5 > 0 else (-1.0 if r5 < 0 else 0.0)
        votes += 0.5 if acc > 0 else (-0.5 if acc < 0 else 0.0)
        # Scale by 5m move in bps, capped
        mag = min(abs(r5) * 10_000 / 40.0, 1.5)
        z = votes * mag
        p = 1.0 / (1.0 + pow(2.718281828, -0.55 * z))
        p = min(max(p, 0.05), 0.95)
        aligned = abs(votes) >= 2.5
        unc = 0.25 if aligned else 0.55
        if mag < 0.15:
            unc = min(0.8, unc + 0.2)
            p = 0.5 + (p - 0.5) * 0.3
        return ExpertView(
            name=self.name,
            p_yes=Decimal(str(round(p, 4))),
            magnitude_bps=Decimal(str(round(abs(r5) * 10_000, 2))),
            uncertainty=Decimal(str(round(unc, 4))),
        )
