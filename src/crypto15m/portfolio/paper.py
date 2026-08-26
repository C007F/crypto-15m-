from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from crypto15m.domain import Fill, MarketQuote, Permit, PortfolioSnapshot, Position, Side
from crypto15m.risk.governor import ny_date


# NOTE: despite the name this is the LIVE book's position ledger, not paper
# trading. Paper was removed on 2026-08-26; this stayed because it is what
# tracks real positions, cost basis and realised P&L. Renaming it means
# touching the live trading path for cosmetics, which is not worth the risk.
class PaperPortfolio:
    """Simulated Kalshi account. Marks Yes positions at yes_bid (exit), No at no_bid."""

    def __init__(self, starting_equity: Decimal, *, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        self.starting_equity = starting_equity
        self.cash = starting_equity
        self.positions: dict[str, Position] = {}
        self.fills: list[Fill] = []
        self.day_start_equity = starting_equity
        self.high_water = starting_equity
        self.realized_pnl_today = Decimal("0")
        self.trades_today = 0
        self.ny_date = ny_date(now)
        self._marks: dict[str, MarketQuote] = {}
        self.last_note = "init"

    def roll_if_new_day(self, now: datetime) -> bool:
        today = ny_date(now)
        if today == self.ny_date:
            return False
        self.ny_date = today
        self.day_start_equity = self.equity
        self.high_water = max(self.high_water, self.equity)
        self.realized_pnl_today = Decimal("0")
        self.trades_today = 0
        return True

    def mark(self, market: MarketQuote) -> None:
        self._marks[market.ticker] = market

    @property
    def equity(self) -> Decimal:
        return self.cash + self.unrealized()

    def unrealized(self) -> Decimal:
        total = Decimal("0")
        for pos in self.positions.values():
            q = self._marks.get(pos.ticker)
            if not q:
                continue
            exit_px = q.yes_bid if pos.side == "yes" else q.no_bid
            total += pos.contracts * (exit_px - pos.avg_price)
        return total

    def open_exposure(self) -> Decimal:
        return sum((p.contracts * p.avg_price for p in self.positions.values()), Decimal("0"))

    def snapshot(self, now: datetime) -> PortfolioSnapshot:
        eq = self.equity
        if eq > self.high_water:
            self.high_water = eq
        return PortfolioSnapshot(
            asof=now,
            ny_date=self.ny_date,
            cash=self.cash,
            equity=eq,
            day_start_equity=self.day_start_equity,
            high_water=self.high_water,
            realized_pnl_today=self.realized_pnl_today,
            open_exposure=self.open_exposure(),
            open_positions=len(self.positions),
            trades_today=self.trades_today,
            tickers=tuple(self.positions.keys()),
        )

    def apply_open(
        self,
        *,
        now: datetime,
        ticker: str,
        side: Side,
        contracts: Decimal,
        price: Decimal,
        fee: Decimal,
        permit: Permit | None,
    ) -> Fill:
        cost = contracts * price + fee
        if cost > self.cash:
            raise ValueError(f"insufficient cash {self.cash} for cost {cost}")
        self.cash -= cost
        existing = self.positions.get(ticker)
        if existing:
            if existing.side != side:
                raise ValueError("cannot flip side in one window; flatten first")
            n = existing.contracts + contracts
            avg = (existing.avg_price * existing.contracts + price * contracts) / n
            self.positions[ticker] = Position(ticker, side, n, avg, existing.fees + fee)
        else:
            self.positions[ticker] = Position(ticker, side, contracts, price, fee)
        self.trades_today += 1
        fill = Fill(
            ts=now,
            ticker=ticker,
            side=side,
            contracts=contracts,
            price=price,
            fee=fee,
            kind="open",
            permit_id=permit.id if permit else None,
        )
        self.fills.append(fill)
        return fill

    def reduce(self, now: datetime, ticker: str, *, contracts: Decimal, bid: Decimal,
               fee: Decimal, kind: str = "scale_out") -> Fill | None:
        """Close PART of a position, leaving the rest open.

        Scaling out is how a winner can be banked and still be allowed to run:
        take enough off to lock a profit, let the remainder ride to fair value
        or settlement. Cost basis is unchanged on the remainder - the fees
        already paid belong to the whole position, so they stay with it
        pro-rata rather than being charged twice.
        """
        pos = self.positions.get(ticker)
        if not pos or contracts <= 0:
            return None
        n = min(Decimal(contracts), pos.contracts)
        if n >= pos.contracts:
            return self.flatten(now, ticker, bid=bid, fee=fee, kind=kind)
        share = n / pos.contracts
        entry_fees = pos.fees * share
        proceeds = n * bid
        pnl = proceeds - n * pos.avg_price - entry_fees - fee
        self.cash += proceeds - fee
        self.realized_pnl_today += pnl
        self.positions[ticker] = Position(
            pos.ticker, pos.side, pos.contracts - n, pos.avg_price,
            pos.fees - entry_fees)
        fill = Fill(ts=now, ticker=ticker, side=pos.side, contracts=n,
                    price=bid, fee=fee, kind=kind)
        self.fills.append(fill)
        return fill

    def flatten(self, now: datetime, ticker: str, *, bid: Decimal, fee: Decimal, kind: str = "flatten") -> Fill | None:
        pos = self.positions.get(ticker)
        if not pos:
            return None
        proceeds = pos.contracts * bid
        pnl = proceeds - pos.contracts * pos.avg_price - pos.fees - fee
        self.cash += proceeds - fee
        self.realized_pnl_today += pnl
        fill = Fill(ts=now, ticker=ticker, side=pos.side, contracts=pos.contracts, price=bid, fee=fee, kind=kind)
        self.fills.append(fill)
        del self.positions[ticker]
        return fill

    def flatten_all(self, now: datetime, fee_fn) -> list[Fill]:
        out: list[Fill] = []
        for ticker in list(self.positions):
            q = self._marks.get(ticker)
            pos = self.positions[ticker]
            if not q:
                continue
            bid = q.yes_bid if pos.side == "yes" else q.no_bid
            fee = fee_fn(pos.contracts, bid)
            fill = self.flatten(now, ticker, bid=bid, fee=fee)
            if fill:
                out.append(fill)
        return out

    def settle(self, now: datetime, ticker: str, result: str) -> Fill | None:
        pos = self.positions.get(ticker)
        if not pos:
            return None
        won = (pos.side == "yes" and result == "yes") or (pos.side == "no" and result == "no")
        payout = pos.contracts * (Decimal("1") if won else Decimal("0"))
        pnl = payout - pos.contracts * pos.avg_price - pos.fees
        self.cash += payout
        self.realized_pnl_today += pnl
        fill = Fill(
            ts=now,
            ticker=ticker,
            side=pos.side,
            contracts=pos.contracts,
            price=Decimal("1") if won else Decimal("0"),
            fee=Decimal("0"),
            kind="settle",
        )
        self.fills.append(fill)
        del self.positions[ticker]
        return fill

    def to_state(self) -> dict:
        return {
            "starting_equity": str(self.starting_equity),
            "cash": str(self.cash),
            "day_start_equity": str(self.day_start_equity),
            "high_water": str(self.high_water),
            "realized_pnl_today": str(self.realized_pnl_today),
            "trades_today": self.trades_today,
            "ny_date": self.ny_date,
            "last_note": self.last_note,
            "positions": {
                t: {
                    "ticker": p.ticker,
                    "side": p.side,
                    "contracts": str(p.contracts),
                    "avg_price": str(p.avg_price),
                    "fees": str(p.fees),
                }
                for t, p in self.positions.items()
            },
        }

    def load_state(self, data: dict) -> None:
        self.starting_equity = Decimal(str(data.get("starting_equity", self.starting_equity)))
        self.cash = Decimal(str(data["cash"]))
        self.day_start_equity = Decimal(str(data["day_start_equity"]))
        self.high_water = Decimal(str(data["high_water"]))
        self.realized_pnl_today = Decimal(str(data.get("realized_pnl_today", "0")))
        self.trades_today = int(data.get("trades_today") or 0)
        self.ny_date = data.get("ny_date") or self.ny_date
        self.last_note = data.get("last_note") or self.last_note
        self.positions = {}
        for t, p in (data.get("positions") or {}).items():
            self.positions[t] = Position(
                ticker=p["ticker"],
                side=p["side"],
                contracts=Decimal(str(p["contracts"])),
                avg_price=Decimal(str(p["avg_price"])),
                fees=Decimal(str(p.get("fees") or "0")),
            )
