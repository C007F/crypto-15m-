import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from crypto15m.domain import MarketQuote, SpotQuote
from crypto15m.experts.baseline import BaselineExpert
from crypto15m.meta.gate import gate
from crypto15m.perception.features import build_frame


def frame(spot, strike, remaining_sec, vol="120"):
    now = datetime(2026, 8, 22, 20, 0, tzinfo=timezone.utc)
    m = MarketQuote(
        ticker="KXBTC15M-TEST",
        event_ticker="KXBTC15M",
        strike=Decimal(strike),
        yes_bid=Decimal("0.99"),
        yes_ask=Decimal("1.00"),
        no_bid=Decimal("0.00"),
        no_ask=Decimal("0.01"),
        yes_ask_size=100,
        no_ask_size=100,
        status="active",
        volume=0,
        open_interest=0,
        result="",
        open_time=now - timedelta(seconds=900 - remaining_sec),
        close_time=now + timedelta(seconds=remaining_sec),
    )
    z = SpotQuote(bid=Decimal(spot), ask=Decimal(spot), last=Decimal(spot), source="t")
    return build_frame(asof=now, market=m, spot=z, spot_candles=None,
                       ref_candles=None, fallback_vol=Decimal(vol))


class Saturation(unittest.TestCase):
    def test_baseline_distinguishes_70_from_90_bps_late(self):
        """Pre-fix both clamped to 0.99 and the gate froze."""
        p70 = float(BaselineExpert().predict(frame("733.10", "728.00", 45)).p_yes)
        p90 = float(BaselineExpert().predict(frame("734.60", "728.00", 45)).p_yes)
        self.assertGreater(p90, p70)          # pre-fix: both pinned at 0.99
        self.assertGreater(p90, 0.99)          # strong evidence keeps its logit

    def test_gate_moves_when_spot_moves_late(self):
        views_a = [BaselineExpert().predict(frame("733.00", "728.00", 45))]
        views_b = [BaselineExpert().predict(frame("733.25", "728.00", 45))]
        self.assertNotEqual(gate(views_a).p_yes, gate(views_b).p_yes)

    def test_gate_output_stays_conservative(self):
        g = gate([BaselineExpert().predict(frame("740.00", "728.00", 30))])
        self.assertLessEqual(float(g.p_yes), 0.99)
