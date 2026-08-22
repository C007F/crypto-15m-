from __future__ import annotations

from decimal import Decimal

from crypto15m.domain import ExpertView
from crypto15m.perception.features import FeatureFrame


class RegimeExpert:
    """Crypto-complex tape + vol regime. High vol -> pull P(Yes) toward 0.5.

    In the ZEC build this read BTC as the macro driver and ZEC's spread against
    it. BTC has no bigger crypto driver, so ETH stands in as the complex-wide
    tape and spot_ref_5m measures whether BTC is leading or lagging it.
    """

    name = "regime"

    def predict(self, features: FeatureFrame) -> ExpertView:
        complex_ret = float(features.ref_ret_5m)
        lead = float(features.spot_ref_5m)
        regime = float(features.vol_regime)
        # If the complex is dumping, BTC 15m up is less likely; vice versa.
        z = complex_ret * 10_000 / 25.0
        z += lead * 10_000 / 40.0
        p = 1.0 / (1.0 + pow(2.718281828, -0.45 * z))
        if regime > 0:
            p = 0.5 + (p - 0.5) * 0.5
            unc = 0.55
        elif regime < 0:
            unc = 0.28
        else:
            unc = 0.4
        p = min(max(p, 0.08), 0.92)
        return ExpertView(
            name=self.name,
            p_yes=Decimal(str(round(p, 4))),
            magnitude_bps=Decimal(str(round(abs(complex_ret) * 10_000, 2))),
            uncertainty=Decimal(str(round(unc, 4))),
        )
