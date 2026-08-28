from __future__ import annotations

import logging
from decimal import ROUND_DOWN, Decimal

from crypto15m.config import Settings
from crypto15m.costs.edge import pick_side
from crypto15m.costs.fees import taker_fee
from crypto15m.costs.ticks import round_buy_price, yes_sell_price_for_no_buy
from crypto15m.domain import ExpertView, Intent, MarketQuote, Side
from crypto15m.perception.features import FeatureFrame

log = logging.getLogger(__name__)
# Board Memo 003. Process-local; the live.log line is the scoreboard.
_stale_declines = 0


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

    # If the real orderbook could not be read, the quote on this frame is the
    # /markets summary - the pricing that filled nothing. Decline rather than
    # trade blind on it. Memo 003: log a counted line so the next storm we
    # can say how many entries the gate declined instead of arguing about it.
    if getattr(features, "book_stale", False):
        global _stale_declines
        _stale_declines += 1
        log.info("declined: book_stale n=%d ticker=%s",
                 _stale_declines, features.market.ticker)
        return None

    # Price band. The cheap leg only pays inside 0.02-0.50; outside it the
    # measured edge is negative on both ends, so refuse before sizing.
    cheap = min(features.market.yes_ask, features.market.no_ask)
    if not (settings.min_cost <= cheap <= settings.max_cost):
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


def bankable(side: Side, entry_px: Decimal, contracts: Decimal,
             market: MarketQuote, min_net: Decimal = Decimal("0.02")) -> bool:
    """True when we could close right now for a real profit after both fees.

    A 15-minute contract does not have to be held to expiry. If it drifts our
    way we can bank it and free the capital for the next mispricing. Smaller
    wins, far more of them, and no exposure to a late reversal.

    The floor matters: Kalshi charges a taker fee each way, so closing one
    cent up is a net loss. min_net is profit per contract AFTER both fees.
    """
    if contracts <= 0:
        return False
    if side == "yes":
        proceeds = market.yes_bid                 # sell YES into the bid
    else:
        proceeds = Decimal("1") - market.yes_ask  # buy YES back at the ask
    if proceeds <= 0:
        return False
    gross = (proceeds - entry_px) * contracts
    fees = taker_fee(contracts, entry_px) + taker_fee(contracts, proceeds)
    return (gross - fees) >= min_net * contracts


def edge_realised(side: Side, view: ExpertView, market: MarketQuote,
                  min_ticks: int = 2) -> bool:
    """True when the market has caught up to our fair value.

    Micro's edge is a mispricing, not a prediction. We buy at 0.40 believing
    fair is 0.55; when the book moves to 0.55 that edge has been fully
    realised, and holding past it is an unhedged bet at fair value - pure
    variance, in a market where the next signal might be genuinely mispriced.

    So: exit once we can SELL at or above what we think the contract is worth.
    min_ticks keeps a one-cent blip from triggering a round trip that costs
    more in fees than it books.
    """
    fair = Decimal(str(view.p_yes))
    edge_gone = Decimal(min_ticks) / Decimal("100")
    if side == "yes":
        # we exit by selling YES into the bid
        return market.yes_bid >= fair + edge_gone
    # a NO position exits by buying YES back at the ask; it is worth
    # (1 - fair) to us, and we can close it for (1 - yes_ask)
    return (Decimal("1") - market.yes_ask) >= (Decimal("1") - fair) + edge_gone


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
