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
from crypto15m.perception.history import EmpiricalMoves
from crypto15m.perception.features import FeatureFrame, build_frame
from crypto15m.perception.spot_feed import fetch_spot_quote
from crypto15m.ladder import LadderObserver
from crypto15m.costs.fees import taker_fee
from crypto15m.live import FillLog, LiveGuard
from crypto15m.trials import BOOKS, LIVE_BOOKS, TrialBook

# Coins excluded from LIVE trading. ZEC's spread is 8c against 1c on every
# other coin, measured not assumed, so a 5c edge is gone before the trade
# starts. It went 0 for 5 live and carried $1.04 of a $1.05 total loss while
# the other five combined were within a cent of flat.
NO_LIVE_ASSETS = frozenset({"zec", "near"})


class Mind:
    def __init__(self, settings: Settings, *, live: bool = False) -> None:
        self.settings = settings
        self.live = live
        self.client = KalshiClient(settings)
        self.journal = Journal(settings.journal_path)
        # Live keeps its OWN lesson file. Paper's lessons were learned under a
        # different execution model - fills that always succeeded and cuts that
        # never reached an exchange - so importing them raises the live bar on
        # evidence that does not apply. It had micro at an effective 0.14
        # against the 0.08 that produced the measured edge, which is a
        # different strategy from the one being tested.
        self.coach = Coach(settings.data_dir /
                           ("lessons_live.jsonl" if live else "lessons.jsonl"))
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
        # LIVE TRADES MICRO ONLY. The paper board runs five books so the
        # comparison stays honest, but three of them lose money: momentum
        # -$0.18/trade, lotto -$0.19/trade, gate flat. Putting real money
        # behind a book with measured negative expectancy is not a decision
        # anyone should be able to make by forgetting to.
        specs = BOOKS
        if live:
            if settings.asset.key in NO_LIVE_ASSETS:
                raise SystemExit(
                    f"{settings.asset.key} is excluded from live trading: "
                    f"its spread is 8c against 1c elsewhere, and it lost "
                    f"$1.04 of a $1.05 total over 5 trades. "
                    f"Remove it from NO_LIVE_ASSETS to override.")
            specs = tuple(b for b in BOOKS if b.book_id in LIVE_BOOKS)
        self.books = [TrialBook(spec, settings, coach=self.coach,
                                guard=self.guard, fills=self.fills)
                      for spec in specs]

        # A shadow book - a paper-filled copy of micro fed identical frames -
        # ran here until 2026-08-26. Removed with the rest of paper: it burned
        # API calls inside the live traders and its output invited exactly the
        # comparison that made the dashboard misleading, since a simulation
        # filling at quotes we cannot cross is not a benchmark. Execution cost
        # is measured directly now, from fill_px against the book we already
        # record on every order.
        self.spot_candles = CandleStore(settings.asset.spot_product)
        self.ref_candles = CandleStore(settings.asset.ref_product)
        self._candle_ts = 0.0
        # Spread across 12-24s. Seeded from the asset name rather than hash(),
        # which Python randomises per process - the offsets must be stable
        # across restarts or the herd can re-synchronise.
        _seed = sum(ord(c) for c in settings.asset.key)
        self._candle_every = 12.0 + (_seed % 120) / 10.0
        self.ladder = LadderObserver(settings.data_dir)
        # Built lazily on first use, rebuilt daily. A failed build degrades to
        # the Gaussian rather than blocking trading.
        self.history = EmpiricalMoves(settings.asset.spot_product)
        self.vol = settings.default_vol_bps

    def _refresh_candles(self) -> None:
        now = time.time()
        # Jittered interval. All seven traders restart together, so a fixed 15s
        # put their refreshes in lockstep: they all missed the shared cache in
        # the same instant and stampeded Coinbase, which is why BTC-USD - the
        # one product every trader wants - was the only thing taking 429s.
        if now - self._candle_ts < self._candle_every:
            return
        try:
            self.spot_candles.refresh(self.client.session)
            self.ref_candles.refresh(self.client.session)
            self._candle_ts = now
        except Exception:
            pass

    def features(self, now: datetime | None = None) -> FeatureFrame | None:
        now = now or datetime.now(timezone.utc)
        import time as _t
        _book_t0 = _t.monotonic()
        market = self.client.get_open_market()
        self._book_read_at = _t.monotonic()
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
        # Carry through whether get_open_market() actually reached the real
        # orderbook. If it did not, `market` holds the summary quote and the
        # entry gate must decline rather than price off it.
        frame.book_stale = bool(getattr(self.client, "book_stale", False))
        try:
            if self.history.stale:
                self.history.build(self.client.session)
            p = self.history.p_yes(float(frame.distance_bps), frame.remaining_sec)
            if p is not None:
                frame.hist_p_yes = p
        except Exception:
            log.warning("history layer failed; gaussian only", exc_info=True)
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

        # Hand the book-read timestamp to each trial so an order can record
        # how stale its quote was. Rejections that correlate with staleness
        # mean the feed is too slow; ones that do not mean the edge is not
        # reachable at any speed.
        for _b in self.books:
            _b._book_read_at = getattr(self, "_book_read_at", None)
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
