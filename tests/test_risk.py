from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from crypto15m.domain import Denial, Intent, PortfolioSnapshot
from crypto15m.risk.governor import HaltReason, RiskGovernor, RiskLimits


NOW = datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc)  # 14:00 NY


def snap(**kwargs) -> PortfolioSnapshot:
    base = dict(
        asof=NOW,
        ny_date="2026-08-21",
        cash=Decimal("1000"),
        equity=Decimal("1000"),
        day_start_equity=Decimal("1000"),
        high_water=Decimal("1000"),
        realized_pnl_today=Decimal("0"),
        open_exposure=Decimal("0"),
        open_positions=0,
        trades_today=0,
        tickers=(),
    )
    base.update(kwargs)
    return PortfolioSnapshot(**base)


def intent(**kwargs) -> Intent:
    base = dict(
        ticker="KXBTC15M-A",
        side="yes",
        contracts=Decimal("5"),
        limit_price=Decimal("0.50"),
        spread=Decimal("0.04"),
        ev=Decimal("1"),
        p_yes=Decimal("0.7"),
        reason="test",
        window_ticker="KXBTC15M-A",
    )
    base.update(kwargs)
    return Intent(**base)


class RiskGovernorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.g = RiskGovernor(
            RiskLimits(),
            state_path=Path(self.tmp.name) / "risk.json",
            kill_path=Path(self.tmp.name) / "KILL",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_cap_is_min_of_pct_and_usd(self):
        # 2.5% of 1000 = 25, usd=25 → 25
        self.assertEqual(self.g.effective_daily_cap(Decimal("1000")), Decimal("25.00"))
        # 2.5% of 10000 = 250, usd=25 → 25
        self.assertEqual(self.g.effective_daily_cap(Decimal("10000")), Decimal("25"))

    def test_allows_first_trade(self):
        out = self.g.allow(intent(), snap())
        self.assertTrue(hasattr(out, "id"))
        self.assertEqual(out.max_contracts, Decimal("5"))

    def test_daily_loss_blocks(self):
        s = snap(realized_pnl_today=Decimal("-25"), equity=Decimal("975"))
        out = self.g.allow(intent(), s)
        self.assertIsInstance(out, Denial)
        self.assertEqual(self.g.halt, HaltReason.DAILY_LOSS_CAP)
        self.assertTrue(self.g.must_flatten())
        self.assertIn("daily loss", out.reason)

    def test_daily_halt_clears_next_ny_day(self):
        self.g.allow(intent(), snap(realized_pnl_today=Decimal("-25"), equity=Decimal("975")))
        self.assertEqual(self.g.halt, HaltReason.DAILY_LOSS_CAP)
        nxt = snap(
            asof=NOW + timedelta(days=1),
            ny_date="2026-08-22",
            realized_pnl_today=Decimal("0"),
            equity=Decimal("975"),
            day_start_equity=Decimal("975"),
            high_water=Decimal("1000"),
        )
        out = self.g.allow(intent(), nxt)
        self.assertTrue(hasattr(out, "id"))
        self.assertEqual(self.g.halt, HaltReason.NONE)

    def test_drawdown_kill_persists(self):
        # Down 8%+ from high-water but still inside the daily $25 cap vs day-start.
        s = snap(
            equity=Decimal("1010"),
            day_start_equity=Decimal("1000"),
            high_water=Decimal("1100"),
            realized_pnl_today=Decimal("10"),
        )
        out = self.g.allow(intent(), s)
        self.assertEqual(self.g.halt, HaltReason.MAX_DRAWDOWN)
        self.assertTrue(self.g.kill_path.exists())
        self.assertIn("drawdown", out.reason)
        nxt = snap(ny_date="2026-08-22", equity=Decimal("1000"), high_water=Decimal("1000"))
        out2 = self.g.allow(intent(), nxt)
        self.assertEqual(self.g.halt, HaltReason.MAX_DRAWDOWN)
        self.assertIn("drawdown", out2.reason)

    def test_per_trade_shrinks_size(self):
        # 5 * 0.50 = 2.5 < $10, raise price so 5 * 0.90 = 4.5 still under 10
        # use 20 contracts * 0.90 = 18 > 10 → shrink to 11? 10/0.9 = 11.11 → 11, still 9.9
        # limits per_trade=10, price=0.80, 20 contracts → 16 → shrink to 12 (9.6)
        out = self.g.allow(intent(contracts=Decimal("20"), limit_price=Decimal("0.80")), snap())
        self.assertTrue(hasattr(out, "max_contracts"))
        self.assertLessEqual(out.max_contracts * Decimal("0.80"), Decimal("10"))

    def test_one_window(self):
        out = self.g.allow(intent(), snap(tickers=("KXBTC15M-A",)))
        self.assertIn("already in this window", out.reason)

    def test_permit_single_use_and_expiry(self):
        p = self.g.allow(intent(), snap())
        self.assertIsNone(self.g.consume(p, NOW))
        self.assertIsNotNone(self.g.consume(p, NOW))
        p2 = self.g.allow(intent(), snap())
        late = NOW + timedelta(seconds=30)
        self.assertIsNotNone(self.g.consume(p2, late))

    def test_max_trades(self):
        out = self.g.allow(intent(), snap(trades_today=16))
        self.assertEqual(self.g.halt, HaltReason.MAX_TRADES)
        self.assertIn("max trades", out.reason)
