import unittest
from decimal import Decimal

from crypto15m.assets import ASSETS, get


class Assets(unittest.TestCase):
    def test_every_asset_is_self_consistent(self):
        for key, a in ASSETS.items():
            self.assertEqual(a.key, key)
            self.assertTrue(a.series.startswith("KX") and a.series.endswith("15M"), a.series)
            self.assertTrue(a.spot_product.endswith("-USD"), a.spot_product)
            self.assertNotEqual(a.spot_product, a.ref_product, key)
            self.assertGreater(Decimal(a.vol_bps), 0)

    def test_ports_are_unique(self):
        ports = [a.port for a in ASSETS.values()]
        self.assertEqual(len(ports), len(set(ports)))

    def test_alts_reference_btc_and_btc_references_eth(self):
        for key, a in ASSETS.items():
            expected = "ETH-USD" if key == "btc" else "BTC-USD"
            self.assertEqual(a.ref_product, expected, key)

    def test_unknown_asset_is_rejected(self):
        with self.assertRaises(SystemExit):
            get("dogecoin")
