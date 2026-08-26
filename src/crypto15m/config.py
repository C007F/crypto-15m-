from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from crypto15m.assets import Asset, get as get_asset

ROOT = Path(__file__).resolve().parents[2]


def _dec(name: str, default: str) -> Decimal:
    return Decimal(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    mode: str
    kalshi_env: str
    api_key_id: str
    private_key_path: Path
    starting_equity: Decimal
    daily_loss_pct: Decimal
    daily_loss_usd: Decimal
    per_trade_risk_usd: Decimal
    max_exposure_usd: Decimal
    max_trades_per_day: int
    max_drawdown_pct: Decimal
    max_contracts: Decimal
    max_spread: Decimal
    min_edge: Decimal
    uncertainty_k: Decimal
    slippage_ticks: int
    entry_start_sec: int
    entry_cutoff_sec: int
    flatten_cutoff_sec: int
    poll_sec: float
    default_vol_bps: Decimal
    data_dir: Path
    asset: Asset

    @property
    def rest_host(self) -> str:
        if self.kalshi_env == "demo":
            return "https://external-api.demo.kalshi.co"
        return "https://external-api.kalshi.com"

    @property
    def api_prefix(self) -> str:
        return "/trade-api/v2"

    @property
    def rest_base(self) -> str:
        return f"{self.rest_host}{self.api_prefix}"

    @property
    def journal_path(self) -> Path:
        return self.data_dir / "journal.sqlite3"

    @property
    def risk_state_path(self) -> Path:
        return self.data_dir / "risk_state.json"

    @property
    def kill_path(self) -> Path:
        return self.data_dir / "KILL"


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    asset = get_asset(os.getenv("ASSET", "btc"))
    # Each asset keeps its own books, journal and risk state under data/<key>/
    # so one coin's drawdown halt can never touch another's.
    data_dir = Path(os.getenv("DATA_DIR", str(ROOT / "data" / asset.key)))
    data_dir.mkdir(parents=True, exist_ok=True)
    key_path = Path(os.getenv("KALSHI_PRIVATE_KEY_PATH", "kalshi_private.pem"))
    if not key_path.is_absolute():
        key_path = ROOT / key_path
    mode = os.getenv("MODE", "paper").strip().lower()
    if mode not in {"paper", "live"}:
        raise ValueError("MODE must be paper or live")
    env = os.getenv("KALSHI_ENV", "prod").strip().lower()
    if env not in {"prod", "demo"}:
        raise ValueError("KALSHI_ENV must be prod or demo")
    return Settings(
        mode=mode,
        kalshi_env=env,
        api_key_id=os.getenv("KALSHI_API_KEY_ID", "").strip(),
        private_key_path=key_path,
        starting_equity=_dec("STARTING_EQUITY", "1000"),
        daily_loss_pct=_dec("DAILY_LOSS_PCT", "0.025"),
        daily_loss_usd=_dec("DAILY_LOSS_USD", "25"),
        per_trade_risk_usd=_dec("PER_TRADE_RISK_USD", "10"),
        max_exposure_usd=_dec("MAX_EXPOSURE_USD", "50"),
        max_trades_per_day=int(os.getenv("MAX_TRADES_PER_DAY", "16")),
        max_drawdown_pct=_dec("MAX_DRAWDOWN_PCT", "0.08"),
        max_contracts=_dec("MAX_CONTRACTS", "5"),
        max_spread=_dec("MAX_SPREAD", "0.08"),
        min_edge=_dec("MIN_EDGE", "0.08"),
        uncertainty_k=_dec("UNCERTAINTY_K", "0.05"),
        slippage_ticks=int(os.getenv("SLIPPAGE_TICKS", "1")),
        entry_start_sec=int(os.getenv("ENTRY_START_SEC", "90")),
        entry_cutoff_sec=int(os.getenv("ENTRY_CUTOFF_SEC", "75")),
        # Separate from entry: past this point a position rides to settlement
        # rather than paying the spread to cut. Entries may legally happen
        # later than this - the liquid zone is the last few minutes - so the
        # two boundaries are deliberately not the same number.
        flatten_cutoff_sec=int(os.getenv("FLATTEN_CUTOFF_SEC", "150")),
        poll_sec=float(os.getenv("POLL_SEC", "1.0")),
        default_vol_bps=_dec("DEFAULT_VOL_BPS", asset.vol_bps),
        data_dir=data_dir,
        asset=asset,
    )
