import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from crypto15m.config import load_settings
from crypto15m.domain import MarketQuote, SpotQuote
from crypto15m.perception.features import build_frame
from crypto15m.trials import BookSpec, TrialBook, LOTTO_MAX_ASK

NO_OFFER = f"no {int(round(1 / float(LOTTO_MAX_ASK)))}x on offer"


class _Journal:
    def __init__(self): self.rows = []
    def log(self, kind, payload, ticker=None): self.rows.append((kind, payload, ticker))


def market(yes_ask="0.99", no_ask="0.99", remaining=300, yes_size=500, no_size=500):
    now = datetime(2026, 8, 22, 20, 0, tzinfo=timezone.utc)
    return now, MarketQuote(
        ticker="KXBTC15M-LOTTO", event_ticker="KXBTC15M",
        strike=Decimal("728"), yes_bid=Decimal("0.01"), yes_ask=Decimal(yes_ask),
        no_bid=Decimal("0.01"), no_ask=Decimal(no_ask),
        yes_ask_size=yes_size, no_ask_size=no_size,
        status="active", volume=0, open_interest=0, result="",
        open_time=now - timedelta(seconds=900 - remaining),
        close_time=now + timedelta(seconds=remaining),
    )


def frame_for(m, now):
    z = SpotQuote(bid=Decimal("730"), ask=Decimal("730"), last=Decimal("730"), source="t")
    return build_frame(asof=now, market=m, spot=z, spot_candles=None,
                       ref_candles=None, fallback_vol=Decimal("120"))


class Lotto(unittest.TestCase):
    def book(self, tmp):
        import dataclasses, os
        os.environ["DATA_DIR"] = str(tmp)
        s = load_settings()
        return TrialBook(BookSpec("lotto", "LOTTO 18x", "", "longshot"), s)

    def run_step(self, b, m, now):
        return b._longshot(now, frame_for(m, now), _Journal())

    def test_buys_cheap_no_side(self):
        import tempfile, pathlib
        b = self.book(pathlib.Path(tempfile.mkdtemp()))
        now, m = market(yes_ask="0.99", no_ask="0.04")
        note = self.run_step(b, m, now)
        self.assertIn("bought no", note)
        pos = b.portfolio.positions[m.ticker]
        self.assertEqual(pos.side, "no")
        self.assertEqual(pos.contracts, Decimal("25"))     # $1 / 0.04

    def test_skips_when_nothing_cheap(self):
        import tempfile, pathlib
        b = self.book(pathlib.Path(tempfile.mkdtemp()))
        now, m = market(yes_ask="0.60", no_ask="0.42")
        self.assertEqual(self.run_step(b, m, now), NO_OFFER)
        self.assertFalse(b.portfolio.positions)

    def test_just_over_threshold_is_skipped(self):
        import tempfile, pathlib
        b = self.book(pathlib.Path(tempfile.mkdtemp()))
        now, m = market(no_ask="0.11")
        self.assertEqual(self.run_step(b, m, now), NO_OFFER)

    def test_ten_cents_buys(self):
        import tempfile, pathlib
        b = self.book(pathlib.Path(tempfile.mkdtemp()))
        now, m = market(no_ask="0.10")
        self.assertIn("bought no", self.run_step(b, m, now))
        self.assertEqual(b.portfolio.positions[m.ticker].contracts, Decimal("10"))

    def test_once_per_window(self):
        import tempfile, pathlib
        b = self.book(pathlib.Path(tempfile.mkdtemp()))
        now, m = market(no_ask="0.05")
        self.run_step(b, m, now)
        self.assertEqual(self.run_step(b, m, now), "holding window")
        self.assertEqual(len(b.portfolio.positions), 1)

    def test_too_late_to_fill(self):
        import tempfile, pathlib
        b = self.book(pathlib.Path(tempfile.mkdtemp()))
        now, m = market(no_ask="0.03", remaining=5)
        self.assertEqual(self.run_step(b, m, now), "too late")
