from decimal import Decimal
import unittest

from crypto15m.costs.ticks import round_buy_price, tick_size, yes_sell_price_for_no_buy


class TickTests(unittest.TestCase):
    def test_coarse_band(self):
        self.assertEqual(tick_size(Decimal("0.52")), Decimal("0.01"))
        self.assertEqual(round_buy_price(Decimal("0.521")), Decimal("0.53"))

    def test_no_buy_maps_to_yes_ask(self):
        self.assertEqual(yes_sell_price_for_no_buy(Decimal("0.54")), Decimal("0.46"))
