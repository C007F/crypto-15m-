from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from crypto15m.learn import Coach, diagnose


class LearnTests(unittest.TestCase):
    def test_fought_spot_note(self):
        les = diagnose(
            {
                "book": "gate",
                "ticker": "T",
                "side": "yes",
                "p_yes": 0.7,
                "uncertainty": 0.2,
                "yes_ask": 0.4,
                "yes_bid": 0.35,
                "distance_bps": -40,
                "remaining_sec": 400,
            },
            pnl=Decimal("-1.20"),
            how="settle",
            result="no",
        )
        self.assertIn("fought_spot", les["tags"])
        self.assertIn("UP", les["why"])
        self.assertIn("under the strike", les["why"])

    def test_unsure_raises_bar(self):
        tmp = Path(tempfile.mkdtemp()) / "lessons.jsonl"
        c = Coach(tmp)
        self.assertEqual(c.extra_edge("gate"), Decimal("0"))
        for _ in range(3):
            c.record(
                diagnose(
                    {
                        "book": "gate",
                        "side": "yes",
                        "p_yes": 0.55,
                        "uncertainty": 0.7,
                        "yes_ask": 0.5,
                        "yes_bid": 0.45,
                        "distance_bps": 5,
                        "remaining_sec": 500,
                    },
                    pnl=Decimal("-0.40"),
                    how="settle",
                    result="no",
                )
            )
        self.assertGreaterEqual(c.extra_edge("gate"), Decimal("0.03"))
