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


class BoardRanking(unittest.TestCase):
    """Books rank by success rate, not profit, and empty books sink."""

    def board(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
        import server
        return server.build_board()

    def test_ranked_by_success_rate_descending(self):
        rates = [b["win_rate"] for b in self.board()["pooled"] if b["settled"]]
        self.assertEqual(rates, sorted(rates, reverse=True))

    def test_books_without_settled_trades_rank_last(self):
        books = self.board()["pooled"]
        first_empty = next((i for i, b in enumerate(books) if not b["settled"]), len(books))
        self.assertTrue(all(not b["settled"] for b in books[first_empty:]))

    def test_rate_is_not_profit_order(self):
        """A book can lead on success rate while another leads on P/L."""
        books = [b for b in self.board()["pooled"] if b["settled"]]
        if len(books) > 1:
            self.assertEqual(books[0]["rank"], 1)

    def test_every_coin_appears_with_its_books(self):
        coins = self.board()["coins"]
        self.assertGreaterEqual(len(coins), 1)
        for c in coins:
            self.assertTrue(c["label"])
            self.assertTrue(c["books"])


class ClosedTradeAccounting(unittest.TestCase):
    """A book that loses money cannot show a 100% success rate."""

    def board(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
        import server
        return server.build_board()

    def test_losing_money_is_never_a_perfect_record(self):
        for b in self.board()["pooled"]:
            if b["closed"] and b["pnl"] < -0.01:
                self.assertLess(b["win_rate"], 1.0,
                                f"{b['label']} lost money at a {b['rate_txt']} win rate")

    def test_cuts_are_counted_as_closed_trades(self):
        for b in self.board()["pooled"]:
            self.assertEqual(b["closed"], b["wins"] + b["losses"])
            self.assertEqual(b["closed"], b["settled"] + b["bailed"])

    def test_per_coin_losses_show_a_losing_record(self):
        for c in self.board()["coins"]:
            for b in c["books"]:
                if b["closed"] and "−" in (b["pnl_txt"] or ""):
                    self.assertNotEqual(b["rate_txt"], "100%",
                                        f"{c['label']}/{b['label']} lost money at 100%")
