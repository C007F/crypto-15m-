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
