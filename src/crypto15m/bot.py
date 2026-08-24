from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from decimal import Decimal

from crypto15m.config import Settings
from crypto15m.experts.baseline import BaselineExpert
from crypto15m.experts.microstructure import MicrostructureExpert
from crypto15m.experts.momentum import MomentumExpert
from crypto15m.experts.regime import RegimeExpert
from crypto15m.experts.tabular import TabularExpert
from crypto15m.journal.sqlite import Journal
from crypto15m.kalshi.client import KalshiClient
from crypto15m.learn import Coach
from crypto15m.meta.gate import gate
from crypto15m.perception.candles import CandleStore
from crypto15m.perception.features import FeatureFrame, build_frame
from crypto15m.perception.spot_feed import fetch_spot_quote
from crypto15m.ladder import LadderObserver
from crypto15m.costs.fees import taker_fee
from crypto15m.live import FillLog, LiveGuard
from crypto15m.trials import BOOKS, TrialBook


class Mind:
    def __init__(self, settings: Settings, *, live: bool = False) -> None:
        self.settings = settings
        self.live = live
        self.client = KalshiClient(settings)
        self.journal = Journal(settings.journal_path)
        self.coach = Coach(settings.data_dir / "lessons.jsonl")
        model_path = settings.data_dir / "models" / "logistic.json"
        self.experts = [
            BaselineExpert(),
            MicrostructureExpert(),
            MomentumExpert(),
            RegimeExpert(),
            TabularExpert(model_path),
        ]
        # Live-only objects; None in paper so there is no path to them.
        self.guard = (LiveGuard(settings.data_dir.parent / "live_state.json")
                      if live else None)
        self.fills = FillLog(settings.data_dir / "fills.jsonl") if live else None
        self.books = [TrialBook(spec, settings, coach=self.coach,
                                guard=self.guard, fills=self.fills)
                      for spec in BOOKS]
        self.spot_candles = CandleStore(settings.asset.spot_product)
        self.ref_candles = CandleStore(settings.asset.ref_product)
        self._candle_ts = 0.0
        self.ladder = LadderObserver(settings.data_dir)
        self.vol = settings.default_vol_bps

    def _refresh_candles(self) -> None:
        now = time.time()
        if now - self._candle_ts < 15:
            return
        try:
            self.spot_candles.refresh(self.client.session)
            self.ref_candles.refresh(self.client.session)
            self._candle_ts = now
        except Exception:
            pass

    def features(self, now: datetime | None = None) -> FeatureFrame | None:
        now = now or datetime.now(timezone.utc)
        market = self.client.get_open_market()
        if market is None:
            return None
        quote = fetch_spot_quote(self.settings.asset.spot_product, self.client.session)
        self._refresh_candles()
        frame = build_frame(
            asof=now,
            market=market,
            spot=quote,
            spot_candles=self.spot_candles,
            ref_candles=self.ref_candles,
            fallback_vol=self.vol,
        )
        self.vol = frame.vol_bps_15m
        return frame

    def _views(self, frame: FeatureFrame):
        return [e.predict(frame) for e in self.experts]

    def _write_status(self, frame: FeatureFrame | None, views, gated, now: datetime, note: str) -> None:
        books = [b.public(now) for b in self.books]
        leader = max(books, key=lambda b: b["equity"]) if books else None
        payload = {
            "ts": now.isoformat(),
            "note": note,
            "running": True,
            "books": books,
            "leader": leader["id"] if leader else None,
            "lessons": self.coach.recent(8),
            "market": None,
            "experts": {},
        }
        if frame is not None:
            m = frame.market
            payload["market"] = {
                "ticker": m.ticker,
                "clock": _clock(frame.remaining_sec),
                "spot": float(frame.spot.mid),
                "asset": self.settings.asset.label,
                "strike": float(m.strike),
                "distance_bps": float(frame.distance_bps),
                "yes_bid": float(m.yes_bid),
                "yes_ask": float(m.yes_ask),
                "p_yes": float(gated.p_yes) if gated else None,
                "uncertainty": float(gated.uncertainty) if gated else None,
            }
            payload["experts"] = {v.name: {"p": float(v.p_yes), "u": float(v.uncertainty)} for v in views}
        path = self.settings.data_dir / "status.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)

    def step(self, now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)

        # Live gates run first, every tick, so the kill file and the daily cap
        # take effect within one poll rather than at the next window boundary.
        if self.live and self.guard is not None:
            # Fail closed. Falling back to settings.starting_equity meant a
            # network blip made a $200 account look like $1000 and silently
            # disabled the equity floor.
            eq = None
            try:
                bal = self.client.get_balance()
                # Kalshi returns both; prefer the explicit dollars field over
                # dividing cents, so a units change upstream cannot silently
                # make the account look 100x richer.
                if bal.get("balance_dollars") is not None:
                    eq = Decimal(str(bal["balance_dollars"]))
                elif bal.get("balance") is not None:
                    eq = Decimal(str(bal["balance"])) / Decimal("100")
            except Exception:
                log.warning("balance fetch failed", exc_info=True)
            if eq is not None:
                self.guard.roll_day(now, eq)
            why = self.guard.blocked(eq, asset=self.settings.asset.key)
            if why:
                for book in self.books:
                    if book.portfolio.positions:
                        book.portfolio.flatten_all(now, taker_fee)
                self._write_status(None, [], None, now, f"LIVE HALT: {why}")
                return f"LIVE HALT: {why}"
        frame = self.features(now)
        if frame is None:
            self._write_status(None, [], None, now, "no open market")
            return f"no open {self.settings.asset.series}"

        self.ladder.observe(frame.market, frame.remaining_sec)
        for stale in [t for t in self.ladder._open if t != frame.market.ticker]:
            try:
                m = self.client.get_market(stale)
                if m.result in {"yes", "no"}:
                    self.ladder.settle(stale, m.result, now)
            except Exception:
                pass
        for book in self.books:
            book.portfolio.mark(frame.market)
            book.settle_stale(now, frame.market.ticker, self.client.get_market, self.journal)

        views = self._views(frame)
        gated = gate(views)
        self.journal.log(
            "prediction",
            {
                "p_yes": gated.p_yes,
                "uncertainty": gated.uncertainty,
                "experts": {v.name: {"p": v.p_yes, "u": v.uncertainty} for v in views},
                "distance_bps": frame.distance_bps,
            },
            ticker=frame.market.ticker,
        )

        notes = []
        for book in self.books:
            n = book.step(
                now=now,
                frame=frame,
                views=views,
                gated=gated,
                journal=self.journal,
                live=self.live,
                client=self.client,
            )
            notes.append(f"{book.spec.book_id}:{n}")

        line = self._render(frame, gated, " | ".join(notes))
        self._write_status(frame, views, gated, now, line)
        return line

    def _render(self, frame: FeatureFrame, view, note: str) -> str:
        m = frame.market
        left = max(int(frame.remaining_sec), 0)
        mins, secs = divmod(left, 60)
        bits = " ".join(f"{b.spec.book_id}={b.portfolio.equity:.2f}" for b in self.books)
        return (
            f"{m.ticker} {mins}m{secs:02d}s  {self.settings.asset.label} {frame.spot.mid:.2f} vs {m.strike:.4f} "
            f"({frame.distance_bps:+.1f}bps)  YES {m.yes_bid:.2f}/{m.yes_ask:.2f}  "
            f"P(yes)={view.p_yes} u={view.uncertainty}  {bits}  {note}"
        )


def _clock(remaining: float) -> str:
    left = max(int(remaining), 0)
    mins, secs = divmod(left, 60)
    return f"{mins}m {secs:02d}s"


def render_watch(frame: FeatureFrame, view, views=None) -> str:
    m = frame.market
    left = max(int(frame.remaining_sec), 0)
    mins, secs = divmod(left, 60)
    lines = [
        f"{m.ticker}  {mins}m {secs:02d}s left",
        f"  strike ${m.strike:.4f}   spot ${frame.spot.mid:.4f}  ({frame.distance_bps:+.1f} bps)",
        f"  GATE P(yes) {view.p_yes}  uncertainty {view.uncertainty}",
    ]
    if views:
        bits = "  ".join(f"{v.name}={v.p_yes} u={v.uncertainty}" for v in views)
        lines.append(f"  experts {bits}")
    return "\n".join(lines)
