import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from crypto15m.domain import MarketQuote
from crypto15m.ladder import LadderObserver


def mq(yes_ask, no_ask, remaining=300, ticker="T1"):
    now = datetime(2026, 8, 22, 20, 0, tzinfo=timezone.utc)
    return MarketQuote(
        ticker=ticker, event_ticker="KXBTC15M", strike=Decimal("728"),
        yes_bid=Decimal("0.01"), yes_ask=Decimal(yes_ask),
        no_bid=Decimal("0.01"), no_ask=Decimal(no_ask),
        yes_ask_size=100, no_ask_size=100, status="active",
        volume=0, open_interest=0, result="",
        open_time=now - timedelta(seconds=600),
        close_time=now + timedelta(seconds=remaining),
    )


class Ladder(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.obs = LadderObserver(self.dir)
        self.now = datetime(2026, 8, 22, 20, 5, tzinfo=timezone.utc)

    def rows(self):
        try:
            return [json.loads(l) for l in open(self.dir / "ladder.jsonl")]
        except OSError:
            return []

    def test_tracks_minimum_ask_and_outcome(self):
        self.obs.observe(mq("0.12", "0.90"), 300)
        self.obs.observe(mq("0.08", "0.93"), 200)   # cheaper: replaces
        self.obs.observe(mq("0.15", "0.88"), 100)   # dearer: ignored
        self.obs.settle("T1", "yes", self.now)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["min_ask"], 0.08)
        self.assertTrue(rows[0]["won"])

    def test_both_sides_recorded(self):
        self.obs.observe(mq("0.10", "0.25"), 300)
        self.obs.settle("T1", "no", self.now)
        got = {r["side"]: r["won"] for r in self.rows()}
        self.assertEqual(got, {"yes": False, "no": True})

    def test_ignores_expensive_and_late(self):
        self.obs.observe(mq("0.55", "0.60"), 300)   # nothing under 30c
        self.obs.observe(mq("0.05", "0.99"), 5)     # too late to fill
        self.obs.settle("T1", "yes", self.now)
        self.assertEqual(self.rows(), [])

    def test_state_survives_restart(self):
        self.obs.observe(mq("0.07", "0.95"), 300)
        obs2 = LadderObserver(self.dir)              # reload from disk
        obs2.settle("T1", "yes", self.now)
        self.assertEqual(self.rows()[0]["min_ask"], 0.07)
