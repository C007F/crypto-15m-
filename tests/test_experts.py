from datetime import datetime, timezone
from decimal import Decimal
import unittest

from crypto15m.domain import MarketQuote, SpotQuote
from crypto15m.experts.microstructure import MicrostructureExpert
from crypto15m.experts.momentum import MomentumExpert
from crypto15m.perception.features import FeatureFrame
from crypto15m.policy.decision import conviction_size, should_flatten


NOW = datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc)


def frame(**kwargs) -> FeatureFrame:
    m = MarketQuote(
        ticker="T",
        event_ticker="E",
        strike=Decimal("700"),
        yes_bid=Decimal("0.48"),
        yes_ask=Decimal("0.52"),
        no_bid=Decimal("0.48"),
        no_ask=Decimal("0.52"),
        yes_ask_size=Decimal("40"),
        no_ask_size=Decimal("40"),
        open_time=NOW,
        close_time=NOW,
        status="active",
        volume=Decimal("1"),
        open_interest=Decimal("1"),
        result="",
    )
    z = SpotQuote(Decimal("710"), Decimal("710.2"), Decimal("710.1"), "t")
    base = dict(
        asof=NOW,
        market=m,
        spot=z,
        vol_bps_15m=Decimal("80"),
        remaining_sec=180,
        opened_sec=720,
        distance_bps=Decimal("140"),
        implied_p_yes=Decimal("0.50"),
        spread=Decimal("0.04"),
        spot_ret_1m=Decimal("0.002"),
        spot_ret_3m=Decimal("0.004"),
        spot_ret_5m=Decimal("0.006"),
        spot_accel=Decimal("0.001"),
    )
    base.update(kwargs)
    return FeatureFrame(**base)


class ExpertTests(unittest.TestCase):
    def test_micro_lifts_p_when_book_lags_spot(self):
        view = MicrostructureExpert().predict(frame())
        self.assertGreater(view.p_yes, Decimal("0.55"))

    def test_momentum_up_tape(self):
        view = MomentumExpert().predict(frame())
        self.assertGreater(view.p_yes, Decimal("0.5"))

    def test_flatten_yes_when_gate_flips(self):
        from crypto15m.domain import ExpertView

        dead = ExpertView("g", Decimal("0.30"), Decimal("0"), Decimal("0.2"))
        self.assertTrue(should_flatten("yes", dead, 200, 75))
        self.assertFalse(should_flatten("yes", dead, 30, 75))

    def test_conviction_scales(self):
        fat = conviction_size(Decimal("5"), Decimal("0.20"), Decimal("0.08"), Decimal("0.1"))
        thin = conviction_size(Decimal("5"), Decimal("0.09"), Decimal("0.08"), Decimal("0.7"))
        self.assertGreaterEqual(fat, thin)
        self.assertGreaterEqual(thin, Decimal("1"))
