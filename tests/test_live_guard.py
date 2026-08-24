"""Live guard rails. Every test here is a way to lose real money."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from crypto15m.live import STAGES, FillLog, FillRecord, LiveGuard, LiveLimits


class Guard(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.g = LiveGuard(self.d / "live.json")
        self.now = datetime(2026, 8, 24, 15, 0, tzinfo=timezone.utc)
        self.g.roll_day(self.now, Decimal("200"))

    def test_starts_at_one_contract(self):
        self.assertEqual(self.g.max_contracts, 1)

    def test_kill_file_blocks_immediately(self):
        self.assertEqual(self.g.blocked(Decimal("200")), "")
        (self.d / "LIVE_KILL").touch()
        self.assertIn("LIVE_KILL", self.g.blocked(Decimal("200")))

    def test_daily_loss_cap_blocks(self):
        self.g.record_close(Decimal("-14.99"))
        self.assertEqual(self.g.blocked(Decimal("200")), "")
        self.g.record_close(Decimal("-0.02"))
        self.assertIn("daily loss", self.g.blocked(Decimal("200")))

    def test_equity_floor_blocks(self):
        self.assertIn("under floor", self.g.blocked(Decimal("49")))

    def test_size_never_exceeds_the_hard_ceiling(self):
        for _ in range(500):                 # walk every stage
            self.g.note_order()
        self.assertLessEqual(self.g.max_contracts, self.g.limits.max_contracts_hard)
        self.assertEqual(self.g.clamp(Decimal("999")),
                         Decimal(self.g.limits.max_contracts_hard))

    def test_stage_advances_only_after_enough_orders(self):
        need = STAGES[0][1]
        for _ in range(need - 1):
            self.g.note_order()
        self.assertEqual(self.g.state.stage, 0)
        self.g.note_order()
        self.assertEqual(self.g.state.stage, 1)

    def test_state_survives_restart(self):
        for _ in range(STAGES[0][1]):
            self.g.note_order()
        again = LiveGuard(self.d / "live.json")
        self.assertEqual(again.state.stage, 1)
        self.assertEqual(again.max_contracts, STAGES[1][0])

    def test_new_day_resets_loss_but_not_stage(self):
        self.g.record_close(Decimal("-20"))
        for _ in range(STAGES[0][1]):
            self.g.note_order()
        self.g.roll_day(self.now.replace(day=25), Decimal("190"))
        self.assertEqual(self.g.state.realized_today, Decimal("0"))
        self.assertEqual(self.g.state.stage, 1, "stage must not reset overnight")

    def test_limits_are_dollars_not_percentages(self):
        self.assertIsInstance(LiveLimits().daily_loss_usd, Decimal)
        self.assertEqual(LiveLimits().daily_loss_usd, Decimal("15"))


class Fills(unittest.TestCase):
    def test_unfilled_order_is_recorded_not_dropped(self):
        d = Path(tempfile.mkdtemp())
        fl = FillLog(d / "fills.jsonl")
        fl.write(FillRecord(ts="t", asset="btc", ticker="K", side="yes",
                            requested=1, quoted_px="0.40", filled=0, fill_px="",
                            status="canceled", remaining_sec=300.0, p_yes="0.7"))
        rows = [json.loads(l) for l in (d / "fills.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["filled"], 0)
        self.assertIn("btc", fl.summary())

    def test_fill_rate_is_the_metric(self):
        d = Path(tempfile.mkdtemp())
        fl = FillLog(d / "fills.jsonl")
        for filled in (1, 1, 0, 0):
            fl.write(FillRecord(ts="t", asset="zec", ticker="K", side="yes",
                                requested=1, quoted_px="0.40", filled=filled,
                                fill_px="0.40", status="x", remaining_sec=1.0,
                                p_yes="0.5"))
        self.assertIn("50%", fl.summary())
