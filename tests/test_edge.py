from datetime import datetime, timezone
from decimal import Decimal
import unittest

from crypto15m.costs.edge import expected_value, pick_side
from crypto15m.domain import MarketQuote


def market(**kwargs) -> MarketQuote:
    now = datetime(2026, 8, 21, 21, 50, tzinfo=timezone.utc)
    base = dict(
        ticker="KXBTC15M-TEST",
        event_ticker="E",
        strike=Decimal("700"),
        yes_bid=Decimal("0.40"),
        yes_ask=Decimal("0.42"),
        no_bid=Decimal("0.58"),
        no_ask=Decimal("0.60"),
        yes_ask_size=Decimal("40"),
        no_ask_size=Decimal("40"),
        open_time=now,
        close_time=now,
        status="active",
        volume=Decimal("1"),
        open_interest=Decimal("1"),
        result="",
        fee_multiplier=Decimal("1"),
    )
    base.update(kwargs)
    return MarketQuote(**base)


class EdgeTests(unittest.TestCase):
    def test_yes_ev_positive_when_p_high_and_ask_cheap(self):
        ev, px, fee = expected_value(
            p_yes=Decimal("0.90"),
            side="yes",
            ask=Decimal("0.42"),
            contracts=Decimal("5"),
            slippage_ticks=0,
        )
        self.assertGreater(ev, Decimal("1"))
        self.assertEqual(px, Decimal("0.42"))
        self.assertGreater(fee, Decimal("0"))

    def test_filter_rejects_coin_flip_at_50(self):
        report = pick_side(
            p_yes=Decimal("0.50"),
            uncertainty=Decimal("0.5"),
            market=market(),
            min_edge=Decimal("0.08"),
            uncertainty_k=Decimal("0.05"),
            max_contracts=Decimal("5"),
            slippage_ticks=1,
        )
        self.assertIsNone(report.side)

    def test_picks_yes_when_edge_clears_buffer(self):
        report = pick_side(
            p_yes=Decimal("0.95"),
            uncertainty=Decimal("0.2"),
            market=market(),
            min_edge=Decimal("0.08"),
            uncertainty_k=Decimal("0.05"),
            max_contracts=Decimal("5"),
            slippage_ticks=0,
        )
        self.assertEqual(report.side, "yes")
        self.assertGreaterEqual(report.ev_per_contract, report.buffer)
