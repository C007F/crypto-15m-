"""Board Memo 005: do not delete healthy books on a 429ed discover,
do not REST-rush a missing file while the stream is alive, do not
let synthesis drive a subscribe until the 24h shadow has run.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from crypto15m.assets import ASSETS, poll_jitter_sec
from crypto15m.kalshi.stream import (
    SHADOW_CALENDAR,
    synthesize_window_tickers,
    tickers_to_drop,
    write_heartbeat,
)


ET = ZoneInfo("America/New_York")


class NeverDeleteOnAbsence(unittest.TestCase):
    def test_429ed_series_keeps_its_books(self):
        held = {
            "KXBTC15M-26AUG281200-00",
            "KXETH15M-26AUG281200-00",
        }
        want = {"KXBTC15M-26AUG281200-00"}          # ETH missing from the partial list
        ok = {"KXBTC15M"}                            # ETH discover 429ed
        self.assertEqual(tickers_to_drop(held, want, ok), set())

    def test_genuinely_closed_window_is_dropped(self):
        held = {
            "KXBTC15M-26AUG281145-45",
            "KXBTC15M-26AUG281200-00",
        }
        want = {"KXBTC15M-26AUG281200-00"}
        ok = {"KXBTC15M"}
        self.assertEqual(
            tickers_to_drop(held, want, ok),
            {"KXBTC15M-26AUG281145-45"},
        )

    def test_empty_want_with_no_ok_series_drops_nothing(self):
        """The 8/27 spiral: every series 429ed, want={}, old code deleted all."""
        held = {"KXBTC15M-26AUG281200-00", "KXSOL15M-26AUG281200-00"}
        self.assertEqual(tickers_to_drop(held, set(), set()), set())


class CalendarShadow(unittest.TestCase):
    def test_format_matches_known_ticker(self):
        now = datetime(2026, 8, 28, 11, 58, tzinfo=ET)   # 11:58 ET -> close 12:00
        got = synthesize_window_tickers(now)
        self.assertEqual(got["KXBTC15M"], "KXBTC15M-26AUG281200-00")
        self.assertEqual(got["KXDOGE15M"], "KXDOGE15M-26AUG281200-00")

    def test_quarter_hour_suffix(self):
        now = datetime(2026, 8, 28, 12, 14, tzinfo=ET)
        self.assertEqual(
            synthesize_window_tickers(now)["KXBTC15M"],
            "KXBTC15M-26AUG281215-15",
        )

    def test_shadow_flag_is_on(self):
        """Part 2 must not drive subscribe until the chairman flips this."""
        self.assertTrue(SHADOW_CALENDAR)

    def test_subscribe_path_does_not_union_synth(self):
        src = (Path(__file__).resolve().parents[1] /
               "src/crypto15m/kalshi/stream.py").read_text()
        # The live subscribe set is want from discover, not synth.
        self.assertIn("new = want - self.subscribed", src)
        self.assertIn("SHADOW_CALENDAR", src)
        self.assertNotIn("new = (want | synth", src)
        self.assertNotIn("want |= ", src)


class HeartbeatAndJitter(unittest.TestCase):
    def test_heartbeat_fresh_then_defer(self):
        from crypto15m.assets import ASSETS as A
        from crypto15m.config import Settings
        from crypto15m.domain import MarketQuote
        from crypto15m.kalshi.client import KalshiClient
        from decimal import Decimal
        from datetime import datetime, timedelta, timezone

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        settings = Settings(
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
            data_dir=d, asset=A["btc"],
        )
        client = KalshiClient(settings)
        client.BOOKS_DIR = d
        calls = []

        def boom(path, params=None):
            calls.append(path)
            raise RuntimeError("should not REST on first missing-file tick")

        client.public_get = boom  # type: ignore[method-assign]
        # Heartbeat fresh, no book file.
        (d / "heartbeat").write_text(str(time.time()))
        now = datetime.now(timezone.utc)
        q = MarketQuote(
            ticker="KXBTC15M-26AUG281215-15", event_ticker="E",
            strike=Decimal("1"), yes_bid=Decimal("0.4"), yes_ask=Decimal("0.42"),
            no_bid=Decimal("0.58"), no_ask=Decimal("0.60"),
            yes_ask_size=Decimal("1"), no_ask_size=Decimal("1"),
            open_time=now, close_time=now + timedelta(minutes=10),
            status="open", volume=Decimal("0"), open_interest=Decimal("0"),
            result="",
        )
        client.with_book(q)
        self.assertTrue(client.book_stale)
        self.assertEqual(calls, [], "first missing-file tick must not REST")
        # Second tick: one-tick wait is over, REST is allowed.
        try:
            client.with_book(q)
        except RuntimeError:
            pass
        self.assertEqual(len(calls), 1)

    def test_dead_stream_does_not_defer(self):
        """No heartbeat -> REST immediately. That is the legitimate fallback."""
        from crypto15m.assets import ASSETS as A
        from crypto15m.config import Settings
        from crypto15m.domain import MarketQuote
        from crypto15m.kalshi.client import KalshiClient
        from decimal import Decimal
        from datetime import datetime, timedelta, timezone

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        settings = Settings(
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
            data_dir=d, asset=A["btc"],
        )
        client = KalshiClient(settings)
        client.BOOKS_DIR = d
        calls = []

        def boom(path, params=None):
            calls.append(path)
            raise RuntimeError("429")

        client.public_get = boom  # type: ignore[method-assign]
        now = datetime.now(timezone.utc)
        q = MarketQuote(
            ticker="KXBTC15M-26AUG281215-15", event_ticker="E",
            strike=Decimal("1"), yes_bid=Decimal("0.4"), yes_ask=Decimal("0.42"),
            no_bid=Decimal("0.58"), no_ask=Decimal("0.60"),
            yes_ask_size=Decimal("1"), no_ask_size=Decimal("1"),
            open_time=now, close_time=now + timedelta(minutes=10),
            status="open", volume=Decimal("0"), open_interest=Decimal("0"),
            result="",
        )
        client.with_book(q)
        self.assertEqual(len(calls), 1)
        self.assertTrue(client.book_stale)

    def test_jitter_is_plus_minus_150ms_and_stable(self):
        keys = list(ASSETS)
        jitters = [poll_jitter_sec(k) for k in keys]
        for j in jitters:
            self.assertGreaterEqual(j, -0.15)
            self.assertLessEqual(j, 0.15)
        self.assertEqual(poll_jitter_sec("btc"), poll_jitter_sec("btc"))
        self.assertNotEqual(poll_jitter_sec("btc"), poll_jitter_sec("eth"))
        # Seven assets must not all land on the same offset.
        self.assertGreater(len(set(round(j, 3) for j in jitters)), 1)

    def test_write_heartbeat_is_fresh(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # write_heartbeat uses the module BOOKS_DIR; just check the helper
        # produces a parseable timestamp when pointed at a temp file via
        # monkeypatch of HEARTBEAT_PATH... easier: call write then read.
        import crypto15m.kalshi.stream as st
        orig_dir, orig_path = st.BOOKS_DIR, st.HEARTBEAT_PATH
        d = Path(tmp.name)
        st.BOOKS_DIR = d
        st.HEARTBEAT_PATH = d / "heartbeat"
        try:
            write_heartbeat(123.0)
            self.assertEqual((d / "heartbeat").read_text(), "123.0")
        finally:
            st.BOOKS_DIR = orig_dir
            st.HEARTBEAT_PATH = orig_path


if __name__ == "__main__":
    unittest.main()
