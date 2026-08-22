import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from crypto15m.config import load_settings
from crypto15m.trials import BOOKS, BookSpec, TrialBook


class Sizing(unittest.TestCase):
    def book(self, spec):
        import os
        os.environ["DATA_DIR"] = str(Path(tempfile.mkdtemp()))
        return TrialBook(spec, load_settings())

    def test_micro_is_sized_up(self):
        spec = next(b for b in BOOKS if b.book_id == "micro")
        self.assertEqual(spec.size_mult, Decimal("3"))

    def test_multiplier_scales_per_trade_caps_only(self):
        s = load_settings()
        big = self.book(BookSpec("x", "X", "", "named", "microstructure",
                                 size_mult=Decimal("3")))
        one = self.book(BookSpec("y", "Y", "", "named", "microstructure"))
        self.assertEqual(big.governor.limits.max_contracts_per_trade,
                         one.governor.limits.max_contracts_per_trade * 3)
        self.assertEqual(big.governor.limits.per_trade_risk_usd,
                         one.governor.limits.per_trade_risk_usd * 3)
        # shared risk floor must not move
        self.assertEqual(big.governor.limits.daily_loss_usd, s.daily_loss_usd)
        self.assertEqual(big.governor.limits.max_drawdown_pct, s.max_drawdown_pct)
        self.assertEqual(big.governor.limits.max_exposure_usd, s.max_exposure_usd)

    def test_other_books_unchanged(self):
        for b in BOOKS:
            if b.book_id != "micro":
                self.assertEqual(b.size_mult, Decimal("1"), b.book_id)


class ConvictionScaling(unittest.TestCase):
    """A raised ceiling must be spent on conviction, not on every trade."""

    def size(self, ceiling, ev, buffer, unc):
        from crypto15m.policy.decision import conviction_size
        return conviction_size(Decimal(str(ceiling)), Decimal(str(ev)),
                               Decimal(str(buffer)), Decimal(str(unc)))

    def test_weak_signal_stays_small_despite_big_ceiling(self):
        small = self.size(5, "0.02", "0.01", "0.60")
        big = self.size(15, "0.02", "0.01", "0.60")
        self.assertLessEqual(big, 4)
        self.assertGreaterEqual(big, small)

    def test_strong_signal_uses_the_ceiling(self):
        weak = self.size(15, "0.02", "0.01", "0.60")
        strong = self.size(15, "0.20", "0.01", "0.05")
        self.assertGreater(strong, weak * 2)
        self.assertGreaterEqual(strong, 12)

    def test_never_zero(self):
        self.assertGreaterEqual(self.size(15, "0.011", "0.01", "0.95"), 1)
