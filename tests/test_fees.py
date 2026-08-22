from decimal import Decimal
import unittest

from crypto15m.costs.fees import fee_per_contract, taker_fee


class FeeTests(unittest.TestCase):
    def test_peaks_near_coin_flip(self):
        mid = taker_fee(Decimal("100"), Decimal("0.50"))
        wing = taker_fee(Decimal("100"), Decimal("0.10"))
        self.assertGreater(mid, wing)
        self.assertEqual(mid, Decimal("1.75"))

    def test_rounds_up_to_cent(self):
        self.assertEqual(fee_per_contract(Decimal("0.52")), Decimal("0.02"))
