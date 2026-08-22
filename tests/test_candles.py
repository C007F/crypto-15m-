import unittest

from crypto15m.perception.candles import CandleStore


class CandleTests(unittest.TestCase):
    def test_ret_and_seq(self):
        s = CandleStore("BTC-USD")
        s.closes = [100.0 + i for i in range(20)]
        self.assertGreater(s.ret(5), 0)
        seq = s.last_n_rets(5)
        self.assertEqual(len(seq), 5)
        self.assertTrue(all(x > 0 for x in seq))
