from datetime import datetime, timezone
from decimal import Decimal
import tempfile
import unittest
from pathlib import Path

from crypto15m.assets import get as get_asset
from crypto15m.config import Settings
from crypto15m.domain import ExpertView, MarketQuote, SpotQuote
from crypto15m.perception.features import FeatureFrame
from crypto15m.trials import BookSpec, TrialBook


NOW = datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc)


class DummyJournal:
    def log(self, *a, **k):
        return None


class DummyClient:
    def place_order(self, **k):
        return {"dry_run": True, "http_status": 201}


def settings(tmp: Path) -> Settings:
    return Settings(
        mode="paper",
        kalshi_env="prod",
        api_key_id="",
        private_key_path=tmp / "none.pem",
        starting_equity=Decimal("1000"),
        daily_loss_pct=Decimal("0.025"),
        daily_loss_usd=Decimal("25"),
        per_trade_risk_usd=Decimal("10"),
        max_exposure_usd=Decimal("50"),
        max_trades_per_day=16,
        max_drawdown_pct=Decimal("0.08"),
        max_contracts=Decimal("5"),
        max_spread=Decimal("0.20"),
        min_edge=Decimal("0.01"),
        uncertainty_k=Decimal("0.0"),
        slippage_ticks=0,
        entry_start_sec=0,
        entry_cutoff_sec=0,
        min_cost=Decimal("0"),
        max_cost=Decimal("1"),
        flatten_cutoff_sec=0,
        poll_sec=1.0,
        default_vol_bps=Decimal("80"),
        data_dir=tmp,
        asset=get_asset("btc"),
    )


def frame() -> FeatureFrame:
    m = MarketQuote(
        ticker="T1",
        event_ticker="E",
        strike=Decimal("700"),
        yes_bid=Decimal("0.40"),
        yes_ask=Decimal("0.42"),
        no_bid=Decimal("0.58"),
        no_ask=Decimal("0.60"),
        yes_ask_size=Decimal("40"),
        no_ask_size=Decimal("40"),
        open_time=NOW,
        close_time=NOW,
        status="active",
        volume=Decimal("1"),
        open_interest=Decimal("1"),
        result="",
    )
    z = SpotQuote(Decimal("720"), Decimal("720"), Decimal("720"), "t")
    return FeatureFrame(
        asof=NOW,
        market=m,
        spot=z,
        vol_bps_15m=Decimal("40"),
        remaining_sec=180,
        opened_sec=400,
        distance_bps=Decimal("280"),
        implied_p_yes=Decimal("0.41"),
        spread=Decimal("0.02"),
    )


class TrialTests(unittest.TestCase):
    def test_sit_never_trades_and_gate_can(self):
        from crypto15m.trials import BookSpec

        tmp = Path(tempfile.mkdtemp())
        st = settings(tmp)
        sit = TrialBook(BookSpec("sit", "SIT", "c", "sit"), st)
        gate = TrialBook(BookSpec("gate", "GATE", "g", "gate"), st)
        fr = frame()
        views = [
            ExpertView("microstructure", Decimal("0.95"), Decimal("100"), Decimal("0.1")),
            ExpertView("momentum", Decimal("0.9"), Decimal("100"), Decimal("0.1")),
        ]
        gated = ExpertView("gate", Decimal("0.93"), Decimal("100"), Decimal("0.1"))
        sit.step(now=NOW, frame=fr, views=views, gated=gated, journal=DummyJournal(), live=False, client=DummyClient())
        gate.step(now=NOW, frame=fr, views=views, gated=gated, journal=DummyJournal(), live=False, client=DummyClient())
        self.assertEqual(sit.portfolio.cash, Decimal("1000"))
        self.assertEqual(len(sit.portfolio.positions), 0)
        self.assertLess(gate.portfolio.cash, Decimal("1000"))
        self.assertTrue((tmp / "accounts" / "gate.json").exists())
        self.assertTrue((tmp / "accounts" / "sit.json").exists())


class StepWithAnOpenPosition(unittest.TestCase):
    """Board Memo 010. The gap that let trials.py:371 run for three days.

    dcf391e added 103 test lines and every one of them passed against code
    that raised AttributeError on the live path. They were pure-unit tests of
    bankable()/edge_realised() plus source-text assertIn checks that asserted
    the broken line EXISTED - so they went green because it was broken.

    Nothing in the suite drove TrialBook.step() while holding a position, and
    `bool(pos)` short-circuits, so the defect was unreachable from the tests.
    These drive the real call site with a real position.
    """

    def _book(self):
        tmp = Path(tempfile.mkdtemp())
        book = TrialBook(
            BookSpec("micro", "MICRO", "m", "named", "microstructure"),
            settings(tmp))
        return book, tmp

    def _views(self, p_yes="0.95"):
        v = ExpertView("microstructure", Decimal(p_yes), Decimal("100"), Decimal("0.1"))
        return [v], ExpertView("gate", Decimal(p_yes), Decimal("100"), Decimal("0.1"))

    def _step(self, book, fr, views, gated):
        return book.step(now=NOW, frame=fr, views=views, gated=gated,
                         journal=DummyJournal(), live=False, client=DummyClient())

    def test_step_manages_an_open_position(self):
        """THE regression test: a tick holding a position must not raise."""
        book, _ = self._book()
        fr = frame()
        views, gated = self._views()
        self._step(book, fr, views, gated)                 # opens
        self.assertTrue(book.portfolio.positions, "setup: expected a position")
        self._step(book, fr, views, gated)                 # the tick that crashed

    def test_a_held_position_still_ticks_after_many_polls(self):
        """The crash fired every tick, so one clean tick is not enough."""
        book, _ = self._book()
        fr = frame()
        views, gated = self._views()
        self._step(book, fr, views, gated)
        self.assertTrue(book.portfolio.positions)
        for _ in range(25):
            self._step(book, fr, views, gated)

    def test_settings_has_no_params_attribute(self):
        """Names the exact defect: getattr cannot defend an attribute chain."""
        book, _ = self._book()
        self.assertFalse(hasattr(book.settings, "params"))

    def test_exits_are_disarmed_and_hold_the_position(self):
        """Memo 010 vote: recall the crash, arm nothing. A position offered
        every exit trigger at once must still be held."""
        from crypto15m import trials
        self.assertFalse(trials.EXITS_ARMED, "arming needs its own memo and vote")
        book, _ = self._book()
        fr = frame()
        views, gated = self._views()
        self._step(book, fr, views, gated)
        pos = book.portfolio.positions.get(fr.market.ticker)
        self.assertIsNotNone(pos)
        # Mark the book deep in profit AND flip the thesis, so bankable,
        # edge_realised and should_flatten would all fire if armed.
        import dataclasses
        rich = dataclasses.replace(fr.market, yes_bid=Decimal("0.97"),
                                   yes_ask=Decimal("0.98"),
                                   no_bid=Decimal("0.02"), no_ask=Decimal("0.03"))
        fr2 = dataclasses.replace(fr, market=rich)
        flipped, gated2 = self._views("0.02")
        self._step(book, fr2, flipped, gated2)
        self.assertIn(fr.market.ticker, book.portfolio.positions,
                      "disarmed exits must not close the position")

    def test_shadow_records_what_would_have_fired(self):
        """The measurement the board asked for, without acting on it."""
        rows = []

        class Rec:
            def log(self, kind, payload, ticker=None):
                rows.append((kind, payload, ticker))

        book, _ = self._book()
        fr = frame()
        views, gated = self._views()
        book.step(now=NOW, frame=fr, views=views, gated=gated,
                  journal=Rec(), live=False, client=DummyClient())
        import dataclasses
        rich = dataclasses.replace(fr.market, yes_bid=Decimal("0.97"),
                                   yes_ask=Decimal("0.98"),
                                   no_bid=Decimal("0.02"), no_ask=Decimal("0.03"))
        fr2 = dataclasses.replace(fr, market=rich)
        book.step(now=NOW, frame=fr2, views=views, gated=gated,
                  journal=Rec(), live=False, client=DummyClient())
        shadows = [r for r in rows if r[0] == "exit_shadow"]
        self.assertTrue(shadows, "an in-profit position should shadow-log")
        self.assertFalse(shadows[0][1]["armed"])

    def test_shadow_never_raises_into_the_tick(self):
        """An exception here is the defect being recalled. It must not escape."""
        book, _ = self._book()
        fr = frame()
        views, gated = self._views()
        self._step(book, fr, views, gated)

        class Boom:
            def log(self, *a, **k):
                raise RuntimeError("journal down")

        book._shadow.clear()
        book._shadow_exit(book.portfolio.positions[fr.market.ticker],
                          fr, views[0], Boom())
