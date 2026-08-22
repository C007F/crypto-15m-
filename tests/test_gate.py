from decimal import Decimal
import unittest

from crypto15m.domain import ExpertView
from crypto15m.meta.gate import gate


def v(name, p, u):
    return ExpertView(name=name, p_yes=Decimal(str(p)), magnitude_bps=Decimal("10"), uncertainty=Decimal(str(u)))


class GateTests(unittest.TestCase):
    def test_mixes_toward_confident_expert(self):
        mixed = gate([v("a", 0.9, 0.1), v("b", 0.2, 0.8)])
        self.assertGreater(mixed.p_yes, Decimal("0.6"))

    def test_disagreement_raises_uncertainty(self):
        agree = gate([v("a", 0.7, 0.2), v("b", 0.72, 0.2)])
        fight = gate([v("a", 0.2, 0.2), v("b", 0.8, 0.2)])
        self.assertGreater(fight.uncertainty, agree.uncertainty)
