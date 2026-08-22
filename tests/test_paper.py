from datetime import datetime, timezone
from decimal import Decimal
import unittest

from crypto15m.domain import MarketQuote
from crypto15m.portfolio.paper import PaperPortfolio


NOW = datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc)


class PaperTests(unittest.TestCase):
    def test_open_and_settle_win(self):
        p = PaperPortfolio(Decimal("1000"), now=NOW)
        p.apply_open(
            now=NOW,
            ticker="T",
            side="yes",
            contracts=Decimal("5"),
            price=Decimal("0.40"),
            fee=Decimal("0.10"),
            permit=None,
        )
        self.assertEqual(p.cash, Decimal("997.90"))  # 1000 - 2.00 - 0.10
        fill = p.settle(NOW, "T", "yes")
        self.assertIsNotNone(fill)
        self.assertEqual(p.cash, Decimal("1002.90"))
        self.assertEqual(p.realized_pnl_today, Decimal("2.90"))

    def test_flatten_at_bid(self):
        p = PaperPortfolio(Decimal("1000"), now=NOW)
        p.apply_open(
            now=NOW, ticker="T", side="yes", contracts=Decimal("2"), price=Decimal("0.50"), fee=Decimal("0"), permit=None
        )
        q = MarketQuote(
            ticker="T",
            event_ticker="E",
            strike=Decimal("1"),
            yes_bid=Decimal("0.40"),
            yes_ask=Decimal("0.42"),
            no_bid=Decimal("0.58"),
            no_ask=Decimal("0.60"),
            yes_ask_size=Decimal("1"),
            no_ask_size=Decimal("1"),
            open_time=NOW,
            close_time=NOW,
            status="active",
            volume=Decimal("0"),
            open_interest=Decimal("0"),
            result="",
        )
        p.mark(q)
        p.flatten(NOW, "T", bid=Decimal("0.40"), fee=Decimal("0.02"))
        # paid 1.00, received 0.80 - 0.02 fee → cash 1000-1+0.78=999.78, pnl -0.22
        self.assertEqual(p.cash, Decimal("999.78"))
        self.assertEqual(p.realized_pnl_today, Decimal("-0.22"))
        self.assertEqual(p.positions, {})
