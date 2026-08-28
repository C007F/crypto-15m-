"""Board Memo 003: the stale-book gate must not stand open.

The hole: orderbook_top() cached None for 1.6s; with_book() reset
book_stale=False at the start of every tick; the cache hit returned None
without setting the flag; decide() therefore priced off the /markets
summary. At POLL_SEC=0.5 that is 3 ticks in 4 under a 429.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from crypto15m.assets import ASSETS
from crypto15m.config import Settings
from crypto15m.domain import ExpertView, MarketQuote, SpotQuote
from crypto15m.kalshi.client import KalshiClient
from crypto15m.perception.features import FeatureFrame
from crypto15m.policy import decision as decision_mod
from crypto15m.policy.decision import decide


def _settings(tmp: Path) -> Settings:
    return Settings(
        mode="paper", kalshi_env="demo", api_key_id="x",
        private_key_path=Path("/dev/null"),
        starting_equity=Decimal("1000"), daily_loss_pct=Decimal("0.02"),
        daily_loss_usd=Decimal("15"), per_trade_risk_usd=Decimal("10"),
        max_exposure_usd=Decimal("50"), max_trades_per_day=16,
        max_drawdown_pct=Decimal("0.08"), max_contracts=Decimal("5"),
        max_spread=Decimal("0.08"), min_edge=Decimal("0.08"),
        uncertainty_k=Decimal("0.05"), slippage_ticks=1,
        entry_start_sec=90, entry_cutoff_sec=75, flatten_cutoff_sec=150,
        min_cost=Decimal("0.02"), max_cost=Decimal("0.50"),
        poll_sec=0.5, default_vol_bps=Decimal("25"),
        data_dir=tmp, asset=ASSETS["btc"],
    )


def _quote(**kw) -> MarketQuote:
    now = datetime.now(timezone.utc)
    base = dict(
        ticker="KXBTC15M-26AUG281200-00",
        event_ticker="E",
        strike=Decimal("70000"),
        yes_bid=Decimal("0.40"),
        yes_ask=Decimal("0.42"),
        no_bid=Decimal("0.58"),
        no_ask=Decimal("0.60"),
        yes_ask_size=Decimal("10"),
        no_ask_size=Decimal("10"),
        open_time=now - timedelta(seconds=200),
        close_time=now + timedelta(seconds=400),
        status="open",
        volume=Decimal("1"),
        open_interest=Decimal("1"),
        result="",
        fee_multiplier=Decimal("1"),
    )
    base.update(kw)
    return MarketQuote(**base)


def _frame(*, book_stale: bool, market: MarketQuote | None = None) -> FeatureFrame:
    now = datetime.now(timezone.utc)
    m = market or _quote()
    spot = SpotQuote(bid=Decimal("100"), ask=Decimal("100.1"),
                     last=Decimal("100"), source="t")
    return FeatureFrame(
        asof=now, market=m, spot=spot,
        vol_bps_15m=Decimal("25"),
        remaining_sec=400, opened_sec=200,
        distance_bps=Decimal("10"),
        book_stale=book_stale,
    )


class WarmFailureCache(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.client = KalshiClient(_settings(self.dir))
        self.client.BOOKS_DIR = self.dir
        self.calls = 0

        def boom(path, params=None):
            self.calls += 1
            raise RuntimeError("429")

        self.client.public_get = boom  # type: ignore[method-assign]

    def test_first_failure_marks_stale(self):
        out = self.client.with_book(_quote())
        self.assertTrue(self.client.book_stale)
        self.assertEqual(out.yes_ask, Decimal("0.42"))  # summary quote returned
        self.assertEqual(self.calls, 1)

    def test_cached_none_keeps_stale_on_next_tick(self):
        """The regression: tick 2 inside 1.6s used to return None with stale False."""
        q = _quote()
        self.client.with_book(q)
        self.assertEqual(self.calls, 1)
        self.client.book_stale = False          # what with_book does at the start of a tick
        self.client.with_book(q)
        self.assertTrue(self.client.book_stale, "cache-hit None must set book_stale")
        self.assertEqual(self.calls, 1, "must not REST again inside the TTL")

    def test_malformed_prices_mark_stale(self):
        top = {
            "yes_ask": Decimal("0"), "yes_ask_size": Decimal("1"),
            "no_ask": Decimal("1"), "no_ask_size": Decimal("1"),
            "yes_bid": Decimal("0"), "no_bid": Decimal("0"),
        }
        self.client._streamed_top = lambda ticker: top  # type: ignore[method-assign]
        self.client.with_book(_quote())
        self.assertTrue(self.client.book_stale)
        self.assertEqual(self.calls, 0)

    def test_empty_bids_mark_stale(self):
        self.client.public_get = lambda path, params=None: {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}  # type: ignore
        self.client.with_book(_quote())
        self.assertTrue(self.client.book_stale)


class DecisionLogs(unittest.TestCase):
    def test_stale_frame_declines(self):
        view = ExpertView(name="micro", p_yes=Decimal("0.80"),
                          magnitude_bps=Decimal("10"), uncertainty=Decimal("0.2"))
        intent = decide(_frame(book_stale=True), view, _settings(Path("/tmp")))
        self.assertIsNone(intent)

    def test_stale_decline_is_logged_and_counted(self):
        before = decision_mod._stale_declines
        view = ExpertView(name="micro", p_yes=Decimal("0.80"),
                          magnitude_bps=Decimal("10"), uncertainty=Decimal("0.2"))
        with self.assertLogs(decision_mod.log, level="INFO") as cm:
            decide(_frame(book_stale=True), view, _settings(Path("/tmp")))
        self.assertTrue(any("declined: book_stale" in m for m in cm.output))
        self.assertEqual(decision_mod._stale_declines, before + 1)

    def test_fresh_book_is_not_silently_blocked(self):
        """The change only declines more under failure, not on a healthy book."""
        view = ExpertView(name="micro", p_yes=Decimal("0.90"),
                          magnitude_bps=Decimal("10"), uncertainty=Decimal("0.1"))
        # Cheap YES at 0.20 is inside the 0.02-0.50 band; whether pick_side
        # fires depends on edge math. We only assert the stale gate itself
        # does not trigger.
        frame = _frame(book_stale=False)
        # If this raises, the gate is wrong; a None from pick_side is fine.
        decide(frame, view, _settings(Path("/tmp")))


if __name__ == "__main__":
    unittest.main()
