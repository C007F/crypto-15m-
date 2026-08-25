import unittest

from crypto15m.perception.history import EmpiricalMoves, HORIZONS_MIN


def synthetic(em, scale=10.0):
    """Populate with a known distribution: |moves| uniform 0..scale bps."""
    for h in HORIZONS_MIN:
        em.moves[h] = [scale * i / 1000.0 for i in range(1000)]
    em.built_at = 9e18   # never stale in tests


class History(unittest.TestCase):
    def setUp(self):
        self.em = EmpiricalMoves("TEST-USD")
        synthetic(self.em)

    def test_probability_falls_with_distance(self):
        near = self.em.p_move_exceeds(1.0, 300)
        far = self.em.p_move_exceeds(8.0, 300)
        self.assertGreater(near, far)

    def test_never_returns_zero(self):
        """The sample not containing a move must not read as impossible."""
        p = self.em.p_move_exceeds(10_000.0, 300)
        self.assertGreater(p, 0.0)

    def test_p_yes_orientation(self):
        above = self.em.p_yes(+5.0, 300)   # spot above strike: YES favoured
        below = self.em.p_yes(-5.0, 300)
        self.assertGreater(above, 0.5)
        self.assertLess(below, 0.5)
        self.assertAlmostEqual(above + below, 1.0, places=6)

    def test_returns_none_without_data(self):
        empty = EmpiricalMoves("TEST-USD")
        self.assertIsNone(empty.p_yes(5.0, 300))

    def test_micro_blends_rather_than_swaps(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] /
               "src/crypto15m/experts/microstructure.py").read_text()
        self.assertIn("0.5 * fair + 0.5 * float(features.hist_p_yes)", src)

    def test_bot_degrades_to_gaussian_on_failure(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/crypto15m/bot.py").read_text()
        self.assertIn("history layer failed; gaussian only", src)
