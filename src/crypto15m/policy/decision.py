from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from crypto15m.config import Settings
from crypto15m.costs.edge import pick_side
from crypto15m.costs.ticks import round_buy_price, yes_sell_price_for_no_buy
from crypto15m.domain import ExpertView, Intent, MarketQuote, Side
from crypto15m.perception.features import FeatureFrame


def conviction_size(max_contracts: Decimal, ev_pc: Decimal, buffer: Decimal, uncertainty: Decimal) -> Decimal:
    extra = float(ev_pc - buffer)
    frac = min(1.0, max(0.25, extra / 0.12))
    frac *= max(0.25, 1.0 - float(uncertainty))
    n = (max_contracts * Decimal(str(frac))).to_integral_value(rounding=ROUND_DOWN)
    return max(n, Decimal("1"))


def decide(
    features: FeatureFrame,
    view: ExpertView,
    settings: Settings,
    extra_edge: Decimal = Decimal("0"),
    max_contracts: Decimal | None = None,
) -> Intent | None:
    """max_contracts is the book's ceiling, not its normal size.

    conviction_size() spends a fraction of it based on leftover EV and
    (1 - uncertainty), so a book with a raised ceiling still trades small on
    ordinary signals and only reaches the ceiling when the edge is large and
    the experts agree.
    """
    if features.opened_sec < settings.entry_start_sec:
        return None
    if features.remaining_sec < settings.entry_cutoff_sec:
        return None
    if features.market.strike <= 0:
        return None
    if features.market.spread > settings.max_spread:
        return None

    report = pick_side(
        p_yes=view.p_yes,
        uncertainty=view.uncertainty,
        market=features.market,
        min_edge=settings.min_edge + extra_edge,
        uncertainty_k=settings.uncertainty_k,
        max_contracts=max_contracts or settings.max_contracts,
        slippage_ticks=settings.slippage_ticks,
    )
    if report.side is None:
        return None

    contracts = conviction_size(report.contracts, report.ev_per_contract, report.buffer, view.uncertainty)
    if report.side == "yes":
        limit = round_buy_price(report.fill_px)
    else:
        limit = report.fill_px

    return Intent(
        ticker=features.market.ticker,
        side=report.side,
        contracts=contracts,
        limit_price=limit,
        spread=features.market.spread,
        ev=report.ev_per_contract * contracts,
        p_yes=view.p_yes,
        reason=report.reason,
        window_ticker=features.market.ticker,
    )


def should_flatten(side: Side, view: ExpertView, remaining_sec: float, cutoff_sec: int) -> bool:
    if remaining_sec < cutoff_sec:
        return False
    if side == "yes" and view.p_yes <= Decimal("0.35"):
        return True
    if side == "no" and view.p_yes >= Decimal("0.65"):
        return True
    return False


def yes_leg_price(market: MarketQuote, intent: Intent):
    if intent.side == "yes":
        return "bid", round_buy_price(intent.limit_price)
    return "ask", yes_sell_price_for_no_buy(intent.limit_price)
