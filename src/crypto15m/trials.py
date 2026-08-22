from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from crypto15m.config import Settings
from crypto15m.costs.fees import taker_fee
from crypto15m.domain import Denial, ExpertView, Intent, MarketQuote, Permit
from crypto15m.learn import Coach, diagnose
from crypto15m.perception.features import FeatureFrame
from crypto15m.policy.decision import decide, should_flatten, yes_leg_price
from crypto15m.portfolio.paper import PaperPortfolio
from crypto15m.risk.governor import RiskGovernor, RiskLimits


@dataclass(frozen=True)
class BookSpec:
    book_id: str
    label: str
    blurb: str
    kind: str
    expert_name: str | None = None
    size_mult: Decimal = Decimal("1")   # scales this book's per-trade caps only


BOOKS = (
    BookSpec("gate", "GATE", "Mix of all experts", "gate"),
    BookSpec("micro", "MICRO", "Stale Kalshi odds vs spot. Up to 3x on conviction.",
             "named", "microstructure", size_mult=Decimal("3")),
    BookSpec("momentum", "MOMENTUM", "1/3/5m BTC tape", "named", "momentum"),
    BookSpec("lotto", "LOTTO 10x", "$1 on any side paying >=10x. Cam's thesis vs longshot bias.", "longshot"),
    BookSpec("sit", "SIT", "Never trades. Control.", "sit"),
)

# Longshot book: buy when a side's ask implies >=10x gross payout (ask <=
# $0.10 on cent ticks), $1 stake, one entry per window, hold to settlement.
# Break-even win rate at $0.10 is 10% plus fees. The thesis is that these win
# more often than priced; the favorite-longshot bias says less. Data decides.
LOTTO_MAX_ASK = Decimal(os.getenv("LOTTO_MAX_ASK", "0.10"))
LOTTO_STAKE = Decimal("1.00")
LOTTO_MIN_REMAINING = 10.0          # need a market that can still fill


def _money(x: Decimal) -> str:
    sign = "+" if x >= 0 else "−"
    return f"{sign}${abs(x):,.2f}"


class TrialBook:
    def __init__(self, spec: BookSpec, settings: Settings, coach: Coach | None = None) -> None:
        self.spec = spec
        self.settings = settings
        self.coach = coach
        self.portfolio = PaperPortfolio(settings.starting_equity)
        # size_mult scales only the per-trade caps. Daily loss, drawdown and
        # exposure limits stay at their settings values, so a bigger book
        # reaches its stop sooner rather than risking more overall.
        mult = spec.size_mult
        self.governor = RiskGovernor(
            RiskLimits(
                daily_loss_pct=settings.daily_loss_pct,
                daily_loss_usd=settings.daily_loss_usd,
                per_trade_risk_usd=settings.per_trade_risk_usd * mult,
                max_exposure_usd=settings.max_exposure_usd,
                max_trades_per_day=settings.max_trades_per_day,
                max_drawdown_pct=settings.max_drawdown_pct,
                max_contracts_per_trade=settings.max_contracts * mult,
                max_spread=settings.max_spread,
            ),
            state_path=settings.data_dir / "accounts" / f"{spec.book_id}.risk.json",
            kill_path=settings.data_dir / "accounts" / f"{spec.book_id}.KILL",
        )
        self.path = settings.data_dir / "accounts" / f"{spec.book_id}.json"
        self._spent: set[str] = set()
        self._entries: dict[str, dict] = {}
        self.last_action = "boot"
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.portfolio.load_state(data["portfolio"])
        self._spent = set(data.get("spent") or [])
        self._entries = data.get("entries") or {}
        self.last_action = data.get("last_action") or self.last_action

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "book_id": self.spec.book_id,
            "last_action": self.last_action,
            "spent": sorted(self._spent),
            "entries": self._entries,
            "portfolio": self.portfolio.to_state(),
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def pick_view(self, views: list[ExpertView], gated: ExpertView) -> ExpertView | None:
        if self.spec.kind == "sit":
            return None
        if self.spec.kind == "gate":
            return gated
        for v in views:
            if v.name == self.spec.expert_name:
                return v
        return gated

    def public(self, now: datetime) -> dict:
        snap = self.portfolio.snapshot(now)
        start = self.portfolio.starting_equity
        pnl = snap.equity - start
        unreal = self.portfolio.unrealized()
        realized = self.portfolio.realized_pnl_today
        if pnl > Decimal("0.004"):
            tag = "PROFIT"
        elif pnl < Decimal("-0.004"):
            tag = "LOSS"
        else:
            tag = "FLAT"
        pos = next(iter(self.portfolio.positions.values()), None)
        pos_txt = "no open trade"
        if pos:
            pos_txt = f"OPEN {pos.side.upper()} × {pos.contracts} @ {pos.avg_price}"
        extra = self.coach.extra_edge(self.spec.book_id) if self.coach else Decimal("0")
        return {
            "id": self.spec.book_id,
            "label": self.spec.label,
            "blurb": self.spec.blurb,
            "equity": float(snap.equity),
            "equity_txt": f"${snap.equity:,.2f}",
            "pnl": float(pnl),
            "pnl_txt": _money(pnl),
            "pnl_tag": tag,
            "realized_txt": _money(realized),
            "open_txt": _money(unreal),
            "pct_txt": f"{(pnl / start * 100):+.2f}%",
            "cash": float(snap.cash),
            "trades_today": snap.trades_today,
            "halt": self.governor.halt.value,
            "action": self.last_action,
            "position_txt": pos_txt,
            "lesson_bar": f"+{extra:.2f} extra edge bar" if extra > 0 else "no extra caution yet",
            "position": None
            if not pos
            else {
                "side": pos.side,
                "n": str(pos.contracts),
                "px": str(pos.avg_price),
                "ticker": pos.ticker,
            },
        }

    def _review(self, ticker: str, pnl: Decimal, how: str, result: str = "") -> None:
        if pnl >= Decimal("-0.01") or self.coach is None:
            return
        entry = self._entries.pop(ticker, None) or {
            "book": self.spec.book_id,
            "ticker": ticker,
            "side": "",
        }
        entry["book"] = self.spec.book_id
        lesson = diagnose(entry, pnl=pnl, how=how, result=result)
        self.coach.record(lesson)
        self.last_action = f"lesson: {lesson['why'][:80]}"

    def step(
        self,
        *,
        now: datetime,
        frame: FeatureFrame,
        views: list[ExpertView],
        gated: ExpertView,
        journal,
        live: bool,
        client,
    ) -> str:
        self.portfolio.mark(frame.market)
        rolled = self.portfolio.roll_if_new_day(now)
        snap = self.portfolio.snapshot(now)
        if rolled:
            self.governor.roll_day(snap)
            self._spent.clear()
            journal.log("day_roll", {"book": self.spec.book_id, "equity": snap.equity}, frame.market.ticker)

        halt = self.governor.observe(snap)
        if self.governor.must_flatten() and snap.open_positions:
            for ticker in list(self.portfolio.positions):
                pos = self.portfolio.positions[ticker]
                q = self.portfolio._marks.get(ticker)
                bid = (q.yes_bid if pos.side == "yes" else q.no_bid) if q else pos.avg_price
                fee = taker_fee(pos.contracts, bid)
                before = self.portfolio.realized_pnl_today
                self.portfolio.flatten(now, ticker, bid=bid, fee=fee)
                self._review(ticker, self.portfolio.realized_pnl_today - before, "flatten")
            self.last_action = f"flatten {halt.value}"
            self.save()
            return self.last_action

        if self.spec.kind == "longshot":
            note = self._longshot(now, frame, journal)
            self.save()
            return note

        view = self.pick_view(views, gated)
        if view is None:
            self.last_action = "sit control"
            self.save()
            return self.last_action

        pos = self.portfolio.positions.get(frame.market.ticker)
        if pos and should_flatten(pos.side, view, frame.remaining_sec, self.settings.entry_cutoff_sec):
            bid = frame.market.yes_bid if pos.side == "yes" else frame.market.no_bid
            fee = taker_fee(pos.contracts, bid, frame.market.fee_multiplier)
            before = self.portfolio.realized_pnl_today
            self.portfolio.flatten(now, frame.market.ticker, bid=bid, fee=fee)
            self._review(frame.market.ticker, self.portfolio.realized_pnl_today - before, "flatten")
            if not str(self.last_action).startswith("lesson"):
                self.last_action = f"exit {pos.side}"
            journal.log("invalidate", {"book": self.spec.book_id, "side": pos.side, "p_yes": view.p_yes}, frame.market.ticker)
            self.save()
            return self.last_action

        if frame.market.ticker in self._spent:
            self.last_action = "holding window"
            self.save()
            return self.last_action

        extra = self.coach.extra_edge(self.spec.book_id) if self.coach else Decimal("0")
        intent = decide(frame, view, self.settings, extra_edge=extra,
                        max_contracts=self.settings.max_contracts * self.spec.size_mult)
        if intent is None:
            self.last_action = "no edge" if extra == 0 else f"no edge (raised bar +{extra})"
            self.save()
            return self.last_action

        snap = self.portfolio.snapshot(now)
        decision = self.governor.allow(intent, snap)
        if isinstance(decision, Denial):
            self.last_action = f"blocked {decision.reason}"
            journal.log("risk_deny", {"book": self.spec.book_id, "reason": decision.reason}, intent.ticker)
            self.save()
            return self.last_action

        permit: Permit = decision
        expired = self.governor.consume(permit, now)
        if expired:
            self.last_action = "permit expired"
            self.save()
            return self.last_action

        self._fill(frame, intent, permit, now, journal, live, client, view, views)
        self._spent.add(frame.market.ticker)
        self.last_action = f"bought {intent.side} x{permit.max_contracts}"
        self.save()
        return self.last_action

    def _longshot(self, now: datetime, frame: FeatureFrame, journal) -> str:
        m = frame.market
        if m.ticker in self._spent:
            self.last_action = "holding window"
            return self.last_action
        if frame.remaining_sec < LOTTO_MIN_REMAINING:
            self.last_action = "too late"
            return self.last_action
        side = price = size = None
        if Decimal("0") < m.yes_ask <= LOTTO_MAX_ASK:
            side, price, size = "yes", m.yes_ask, m.yes_ask_size
        elif Decimal("0") < m.no_ask <= LOTTO_MAX_ASK:
            side, price, size = "no", m.no_ask, m.no_ask_size
        if side is None:
            self.last_action = f"no {int(round(1 / float(LOTTO_MAX_ASK)))}x on offer"
            return self.last_action

        contracts = (LOTTO_STAKE / price).to_integral_value(rounding="ROUND_DOWN")
        if size > 0:
            contracts = min(contracts, Decimal(str(size)))
        if contracts < 1:
            self.last_action = "no size"
            return self.last_action
        fee = taker_fee(contracts, price, m.fee_multiplier)
        try:
            self.portfolio.apply_open(
                now=now, ticker=m.ticker, side=side,
                contracts=contracts, price=price, fee=fee, permit=None,
            )
        except ValueError as exc:
            self.last_action = f"skip: {exc}"
            return self.last_action
        self._spent.add(m.ticker)
        self._entries[m.ticker] = {
            "book": self.spec.book_id, "ticker": m.ticker, "side": side,
            "price": str(price), "contracts": str(contracts),
            "payout_x": str(round(1 / float(price), 1)),
        }
        journal.log(
            "longshot_entry",
            {"book": self.spec.book_id, "side": side, "price": price,
             "contracts": contracts, "payout_x": round(1 / float(price), 1)},
            m.ticker,
        )
        self.last_action = f"bought {side} x{contracts} @ {price} ({round(1/float(price))}x)"
        return self.last_action

    def settle_stale(self, now: datetime, current_ticker: str, get_market, journal) -> None:
        for ticker in list(self.portfolio.positions):
            if ticker == current_ticker:
                continue
            try:
                m: MarketQuote = get_market(ticker)
            except Exception:
                continue
            if m.result not in {"yes", "no"}:
                continue
            before = self.portfolio.realized_pnl_today
            fill = self.portfolio.settle(now, ticker, m.result)
            if fill:
                self._review(ticker, self.portfolio.realized_pnl_today - before, "settle", m.result)
                journal.log(
                    "settle",
                    {"book": self.spec.book_id, "result": m.result, "side": fill.side},
                    ticker,
                )
                if not str(self.last_action).startswith("lesson"):
                    self.last_action = f"settled {m.result}"
                self.save()

    def _fill(
        self,
        frame: FeatureFrame,
        intent: Intent,
        permit: Permit,
        now,
        journal,
        live: bool,
        client,
        view: ExpertView,
        views: list[ExpertView],
    ) -> None:
        market = frame.market
        yes_leg, yes_px = yes_leg_price(market, intent)
        count = permit.max_contracts
        price = intent.limit_price
        fee = taker_fee(count, price, market.fee_multiplier)
        resp = client.place_order(
            ticker=market.ticker,
            side=intent.side,
            count=count,
            price=yes_px,
            permit=permit,
            dry_run=not live,
            yes_leg=yes_leg,
        )
        if live and resp.get("http_status") not in (200, 201, None):
            journal.log("order_reject", {"book": self.spec.book_id, "resp": str(resp)[:300]}, market.ticker)
            self.last_action = "order reject"
            return
        self.portfolio.apply_open(
            now=now,
            ticker=market.ticker,
            side=intent.side,
            contracts=count,
            price=price,
            fee=fee,
            permit=permit,
        )
        self._entries[market.ticker] = {
            "book": self.spec.book_id,
            "ticker": market.ticker,
            "side": intent.side,
            "price": str(price),
            "n": str(count),
            "p_yes": float(view.p_yes),
            "uncertainty": float(view.uncertainty),
            "yes_bid": float(market.yes_bid),
            "yes_ask": float(market.yes_ask),
            "distance_bps": float(frame.distance_bps),
            "remaining_sec": float(frame.remaining_sec),
            "spread": float(market.spread),
            "experts": {v.name: float(v.p_yes) for v in views},
        }
        journal.log(
            "fill",
            {
                "book": self.spec.book_id,
                "side": intent.side,
                "n": str(count),
                "px": str(price),
                "fee": str(fee),
                "live": live,
            },
            market.ticker,
        )
