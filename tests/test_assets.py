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


class DashboardPayload(unittest.TestCase):
    """The status contract between trader and dashboard.

    The trader's field was renamed spot; the dashboard kept reading "btc" and
    silently rendered an em dash for the price on every coin.
    """

    def payload(self, asset="btc"):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
        import server
        return server, server.build(asset)

    def test_spot_price_is_populated_for_every_running_coin(self):
        server, _ = self.payload()
        for key in ASSETS:
            st, _, _ = server.paths(key)
            if not st.exists():
                continue
            d = server.build(key)
            if not d.get("ticker"):
                continue          # between windows, nothing to price
            self.assertNotEqual(d["spot"], "—", f"{key} rendered no spot price")
            self.assertTrue(d["dist"], f"{key} rendered no distance")

    def test_sub_dollar_coins_keep_precision(self):
        server, _ = self.payload()
        self.assertEqual(server.build.__globals__["json"].__name__, "json")
        d = server.build("doge")
        if d.get("ticker"):
            # $0.09 vs a $0.09 strike hides the whole signal
            self.assertGreater(len(d["spot"].split(".")[-1]), 2, d["spot"])
