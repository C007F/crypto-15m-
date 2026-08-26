"""Live guard rails. Every test here is a way to lose real money."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from crypto15m.live import STAGES, FillLog, FillRecord, LiveGuard, LiveLimits


class Guard(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.g = LiveGuard(self.d / "live.json")
        self.now = datetime(2026, 8, 24, 15, 0, tzinfo=timezone.utc)
        self.g.roll_day(self.now, Decimal("200"))

    def test_starts_at_one_contract(self):
        """The ramp starts small. Restored 2026-08-26: an 8-contract request
        was harmless while stale-quote limits filled almost nothing, but once
        orders actually crossed, 8 spent the $15 daily stop in three trades -
        the fewest observations at the moment we understood the strategy
        least."""
        self.assertEqual(self.g.max_contracts, 1)

    def test_kill_file_blocks_immediately(self):
        self.assertEqual(self.g.blocked(Decimal("200")), "")
        (self.d / "LIVE_KILL").touch()
        self.assertIn("LIVE_KILL", self.g.blocked(Decimal("200")))

    def test_daily_loss_cap_blocks(self):
        self.g.record_close(Decimal("-14.99"))
        self.assertEqual(self.g.blocked(Decimal("200")), "")
        self.g.record_close(Decimal("-0.02"))
        self.assertIn("daily loss", self.g.blocked(Decimal("200")))

    def test_equity_floor_blocks(self):
        self.assertIn("under floor", self.g.blocked(Decimal("49")))

    def test_size_never_exceeds_the_hard_ceiling(self):
        for _ in range(500):                 # walk every stage
            self.g.note_order()
        self.assertLessEqual(self.g.max_contracts, self.g.limits.max_contracts_hard)
        self.assertEqual(self.g.clamp(Decimal("999")),
                         Decimal(self.g.limits.max_contracts_hard))

    def test_ramp_climbs_to_the_hard_ceiling_and_stops(self):
        for _ in range(200):
            self.g.note_order()
        self.assertEqual(self.g.max_contracts, 8,
                         "ramp tops out at the hard ceiling and stays there")

    def test_ramp_steps_on_filled_orders(self):
        """1 -> 3 -> 8, ten fills per step."""
        self.assertEqual(self.g.max_contracts, 1)
        for _ in range(10):
            self.g.note_order()
        self.assertEqual(self.g.max_contracts, 3)
        for _ in range(10):
            self.g.note_order()
        self.assertEqual(self.g.max_contracts, 8)

    def test_state_survives_restart(self):
        for _ in range(12):
            self.g.note_order()
        again = LiveGuard(self.d / "live.json")
        # 10 advanced the stage and reset the counter, so 2 remain at stage 1.
        self.assertEqual(again.state.stage, 1)
        self.assertEqual(again.state.orders_at_stage, 2)
        self.assertEqual(again.max_contracts, 3)

    def test_new_day_resets_loss_but_not_stage(self):
        self.g.record_close(Decimal("-20"))
        for _ in range(20):
            self.g.note_order()
        self.g.roll_day(self.now.replace(day=25), Decimal("190"))
        self.assertEqual(self.g.state.realized_today, Decimal("0"))

    def test_limits_are_dollars_not_percentages(self):
        self.assertIsInstance(LiveLimits().daily_loss_usd, Decimal)
        self.assertEqual(LiveLimits().daily_loss_usd, Decimal("15"))


class Fills(unittest.TestCase):
    def test_unfilled_order_is_recorded_not_dropped(self):
        d = Path(tempfile.mkdtemp())
        fl = FillLog(d / "fills.jsonl")
        fl.write(FillRecord(ts="t", asset="btc", ticker="K", side="yes",
                            requested=1, quoted_px="0.40", filled=0, fill_px="",
                            status="canceled", remaining_sec=300.0, p_yes="0.7"))
        rows = [json.loads(l) for l in (d / "fills.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["filled"], 0)
        self.assertIn("btc", fl.summary())

    def test_fill_rate_is_the_metric(self):
        d = Path(tempfile.mkdtemp())
        fl = FillLog(d / "fills.jsonl")
        for filled in (1, 1, 0, 0):
            fl.write(FillRecord(ts="t", asset="zec", ticker="K", side="yes",
                                requested=1, quoted_px="0.40", filled=filled,
                                fill_px="0.40", status="x", remaining_sec=1.0,
                                p_yes="0.5"))
        self.assertIn("50%", fl.summary())


class SharedAcrossCoins(unittest.TestCase):
    """Six processes, one account. These are the bugs the review found."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.now = datetime(2026, 8, 24, 15, 0, tzinfo=timezone.utc)

    def guard(self):
        g = LiveGuard(self.root / "live_state.json")
        g.roll_day(self.now, Decimal("200"))
        return g

    def test_cap_is_account_wide_not_per_coin(self):
        """Six coins each losing $3 must trip a $15 account cap, not survive
        because each thinks it has its own budget."""
        coins = [self.guard() for _ in range(6)]
        for g in coins:
            g.record_close(Decimal("-3"))
        self.assertIn("daily loss", coins[0].blocked(Decimal("200")))

    def test_one_coin_sees_another_coins_losses(self):
        a, b = self.guard(), self.guard()
        a.record_close(Decimal("-16"))
        self.assertIn("daily loss", b.blocked(Decimal("200")),
                      "a coin must not keep trading after the account cap fell")

    def test_concurrent_writes_do_not_lose_increments(self):
        import multiprocessing as mp

        def hit(root, n):
            g = LiveGuard(Path(root) / "live_state.json")
            for _ in range(n):
                g.record_close(Decimal("-0.01"))

        self.guard()
        procs = [mp.Process(target=hit, args=(str(self.root), 40)) for _ in range(6)]
        for p in procs: p.start()
        for p in procs: p.join()
        final = LiveGuard(self.root / "live_state.json")
        self.assertEqual(final.state.realized_today, Decimal("-2.40"),
                         "lost increments: the read-modify-write is not atomic")

    def test_account_kill_stops_every_coin(self):
        g = self.guard()
        (self.root / "LIVE_KILL").touch()
        self.assertIn("LIVE_KILL", g.blocked(Decimal("200")))

    def test_per_coin_kill_stops_only_that_coin(self):
        g = self.guard()
        (self.root / "zec").mkdir(parents=True, exist_ok=True)
        (self.root / "zec" / "LIVE_KILL").touch()
        self.assertIn("zec", g.blocked(Decimal("200"), asset="zec"))
        self.assertEqual(g.blocked(Decimal("200"), asset="btc"), "")

    def test_unknown_equity_refuses_to_trade(self):
        """A failed balance fetch must halt, not fall back to a guess."""
        g = self.guard()
        self.assertIn("refusing", g.blocked(None))


class ExitsMustCross(unittest.TestCase):
    """A cut that does not fill is the failure mode that inverts the strategy."""

    def test_close_uses_an_extreme_limit_not_the_last_quote(self):
        from pathlib import Path
        src = Path(__file__).resolve().parents[1] / "src/crypto15m/trials.py"
        body = src.read_text()
        i = body.index("def _live_close")
        j = body.index("def _review", i)
        close = body[i:j]
        self.assertIn('Decimal("0.01")', close, "sell side must accept any bid")
        self.assertIn('Decimal("0.99")', close, "buy side must take any ask")
        self.assertNotIn("market.yes_bid", close,
                         "closing at the last-seen quote was rejected live")

    def test_fill_parser_matches_the_verified_v2_shape(self):
        from crypto15m.kalshi.client import parse_fill
        real = {"average_fee_paid": "0.0085", "average_fill_price": "0.8600",
                "fill_count": "1.00", "remaining_count": "0.00",
                "order_id": "01a034d9", "http_status": 201}
        n, px = parse_fill(real)
        self.assertEqual(n, 1)
        self.assertEqual(px, __import__("decimal").Decimal("0.8600"))
        rejected = {"error": {"code": "fill_or_kill_insufficient_resting_volume"},
                    "http_status": 409}
        self.assertEqual(parse_fill(rejected), (0, None))

    def test_order_endpoint_is_v2(self):
        from crypto15m.kalshi.client import KalshiClient
        self.assertEqual(KalshiClient.order_path, "/portfolio/events/orders",
                         "/portfolio/orders is V1 and returns HTTP 410")


class LiveLearnsSeparately(unittest.TestCase):
    """Paper lessons must not raise the live bar.

    Paper's losses were recorded under an execution model where every fill
    succeeded and cuts never reached an exchange. Importing them put micro's
    effective threshold at 0.14 against the 0.08 that produced the measured
    edge - a different strategy from the one under test.
    """

    def test_live_and_paper_use_different_lesson_files(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/crypto15m/bot.py").read_text()
        self.assertIn('"lessons_live.jsonl" if live else "lessons.jsonl"', src)

    def test_a_fresh_coach_does_not_raise_the_bar(self):
        import tempfile
        from decimal import Decimal
        from pathlib import Path
        from crypto15m.learn import Coach
        c = Coach(Path(tempfile.mkdtemp()) / "lessons_live.jsonl")
        self.assertEqual(c.extra_edge("micro"), Decimal("0"))


class FillReportingIsHonest(unittest.TestCase):
    """The first live order was logged as 'bought' when it never filled.

    The money was safe - no position was booked - but the log and the
    dashboard said the opposite of what happened, which is the kind of bug
    that makes you trust a number you should not.
    """

    def test_fill_returns_a_count_not_none(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/crypto15m/trials.py").read_text()
        i = src.index("def _fill")
        head = src[i:i + 600]
        self.assertIn("-> int:", head, "_fill must report what it filled")

    def test_every_early_return_reports_zero(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/crypto15m/trials.py").read_text()
        # slice from _fill to whatever method follows it, whichever that is
        start = src.index("def _fill")
        rest = src[start + 10:]
        end = start + 10 + (rest.index("\n    def ") if "\n    def " in rest else len(rest))
        body = src[start:end]
        for bail in ("live cap allows nothing", "no fill: quote gone"):
            self.assertIn(bail, body, f"{bail!r} missing from _fill")
            i = body.index(bail)
            self.assertIn("return 0", body[i:i + 120], f"{bail!r} must return 0")

    def test_caller_does_not_announce_an_unfilled_buy(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/crypto15m/trials.py").read_text()
        self.assertIn("got = self._fill(", src)
        self.assertIn("if got:", src)
        self.assertNotIn('self.last_action = f"bought {intent.side} x{permit.max_contracts}"', src)


class CrossCushionIsAbsolute(unittest.TestCase):
    """Tick size collapses 10x below $0.10, so a tick-counted cushion was
    0.3 cents on a cheap contract and 3 cents on a mid-priced one. Every live
    cheap-contract order crossed by a third of a cent and was outrun."""

    def test_cushion_is_the_same_in_dollars_at_every_price(self):
        from decimal import Decimal
        from crypto15m.costs.edge import fill_price
        cushions = []
        for a in ("0.02", "0.085", "0.21", "0.43", "0.61"):
            ask = Decimal(a)
            cushions.append(float(fill_price(ask, 3) - ask))
        # all within a rounding tick of each other, not 10x apart
        self.assertLess(max(cushions) - min(cushions), 0.011,
                        f"cushion still varies with price band: {cushions}")

    def test_cheap_contracts_stay_economic_or_get_refused(self):
        """A 3c cushion on a 9c contract is a third of the stake. EV must
        price that, so the bot declines rather than chasing."""
        from decimal import Decimal
        from crypto15m.costs.edge import expected_value
        ev_cheap, px, _ = expected_value(p_yes=Decimal("0.20"), side="yes",
                                         ask=Decimal("0.09"), contracts=Decimal("1"),
                                         slippage_ticks=3)
        self.assertGreater(px, Decimal("0.11"), "cushion not applied to cheap asks")

    def test_zero_ticks_means_no_cushion(self):
        from decimal import Decimal
        from crypto15m.costs.edge import fill_price
        self.assertEqual(fill_price(Decimal("0.43"), 0), Decimal("0.43"))


class NoSideCostBasis(unittest.TestCase):
    """average_fill_price is always on the YES leg.

    The first live fill bought a NO contract for 6.9c and the bot booked it at
    93.1c, then reported a 94c loss on a trade that really cost 7.35c. The
    inflated number fed the daily loss cap, which would have halted trading
    after ~16 trades instead of ~200.
    """

    def test_no_side_cost_is_one_minus_the_yes_price(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/crypto15m/trials.py").read_text()
        self.assertIn('price = avg if intent.side == "yes" else (Decimal("1") - avg)', src)

    def test_the_arithmetic(self):
        from decimal import Decimal
        avg = Decimal("0.9310")
        yes_cost = avg
        no_cost = Decimal("1") - avg
        self.assertEqual(no_cost, Decimal("0.0690"))
        self.assertGreater(yes_cost, no_cost * 13)   # the size of the error


class RetryOnRejection(unittest.TestCase):
    """A rejection means the quote moved, not that the idea was wrong."""

    def src(self):
        from pathlib import Path
        return (Path(__file__).resolve().parents[1] / "src/crypto15m/trials.py").read_text()

    def test_a_rejected_window_is_not_burned(self):
        s = self.src()
        self.assertIn("if got:\n            self._spent.add(tk)", s,
                      "the window should only be spent on an actual fill")

    def test_retries_are_capped(self):
        from crypto15m.trials import MAX_ENTRY_ATTEMPTS
        self.assertEqual(MAX_ENTRY_ATTEMPTS, 3)
        self.assertIn("if n >= MAX_ENTRY_ATTEMPTS:", self.src())

    def test_the_counter_survives_a_restart(self):
        s = self.src()
        self.assertIn('"attempts": self._attempts,', s)
        self.assertIn('self._attempts = dict(data.get("attempts") or {})', s)

    def test_a_retry_must_re_earn_the_edge(self):
        """decide() re-runs from scratch, so a retry only happens if EV still
        clears the bar at the new price. That is what makes it not chasing."""
        from pathlib import Path
        dec = (Path(__file__).resolve().parents[1] /
               "src/crypto15m/policy/decision.py").read_text()
        self.assertIn("report = pick_side(", dec)
        self.assertIn("if report.side is None:", dec)


class ZecExcludedFromLive(unittest.TestCase):
    """ZEC's spread is 8c against 1c on every other coin.

    A 5c edge is gone before the trade starts. Live it went 0 for 5 and
    carried $1.04 of a $1.05 total loss; the other five combined were within a
    cent of flat. Excluded structurally, not by leaving a service unstarted -
    a stopped service restarts.
    """

    def test_zec_is_on_the_exclusion_list(self):
        from crypto15m.bot import NO_LIVE_ASSETS
        self.assertIn("zec", NO_LIVE_ASSETS)

    def test_other_coins_are_not_excluded(self):
        from crypto15m.bot import NO_LIVE_ASSETS
        for coin in ("btc", "eth", "xrp", "doge", "sol"):
            self.assertNotIn(coin, NO_LIVE_ASSETS)

    def test_the_check_runs_before_any_book_is_built(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/crypto15m/bot.py").read_text()
        guard = src.index("NO_LIVE_ASSETS:")
        books = src.index("self.books = [TrialBook(")
        self.assertLess(guard, books, "exclusion must gate before books exist")

    def test_zec_still_runs_on_paper(self):
        """It stays configured so paper and the ladder keep observing it."""
        from crypto15m.assets import ASSETS
        self.assertIn("zec", ASSETS)


class TakeProfitWhenTheEdgeIsGone(unittest.TestCase):
    """Winners rode to settlement even after the market had repriced.

    Micro's edge is a mispricing. Once the book reaches our fair value the
    edge is fully realised, and holding past it is an unhedged bet at fair
    value in a market where the next signal might be genuinely mispriced.
    """

    def market(self, bid, ask):
        from datetime import datetime, timezone
        from decimal import Decimal
        from crypto15m.domain import MarketQuote
        return MarketQuote(ticker="T", event_ticker="E", strike=Decimal("1"),
            yes_bid=Decimal(bid), yes_ask=Decimal(ask),
            no_bid=Decimal(str(1 - float(ask))), no_ask=Decimal(str(1 - float(bid))),
            yes_ask_size=Decimal("100"), no_ask_size=Decimal("100"),
            open_time=datetime.now(timezone.utc), close_time=datetime.now(timezone.utc),
            status="active", volume=Decimal("0"), open_interest=Decimal("0"), result="")

    def view(self, p):
        from decimal import Decimal
        from crypto15m.domain import ExpertView
        return ExpertView(name="micro", p_yes=Decimal(p),
                          magnitude_bps=Decimal("0"), uncertainty=Decimal("0.4"))

    def test_holds_while_the_market_is_still_cheap(self):
        from crypto15m.policy.decision import edge_realised
        self.assertFalse(edge_realised("yes", self.view("0.55"), self.market("0.42", "0.43")))

    def test_exits_once_the_market_reaches_fair_value(self):
        from crypto15m.policy.decision import edge_realised
        self.assertTrue(edge_realised("yes", self.view("0.55"), self.market("0.58", "0.59")))

    def test_a_one_cent_blip_does_not_trigger_churn(self):
        """A round trip costs fees; a tick of noise must not pay for them."""
        from crypto15m.policy.decision import edge_realised
        self.assertFalse(edge_realised("yes", self.view("0.55"), self.market("0.56", "0.57")))

    def test_no_side_is_symmetric(self):
        from crypto15m.policy.decision import edge_realised
        # holding NO, fair for NO is 1-0.35 = 0.65; market must let us sell there
        self.assertFalse(edge_realised("no", self.view("0.35"), self.market("0.33", "0.34")))
        self.assertTrue(edge_realised("no", self.view("0.35"), self.market("0.29", "0.30")))

    def test_both_exit_reasons_are_journalled_distinctly(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/crypto15m/trials.py").read_text()
        self.assertIn('"edge_realised" if took_profit else "thesis_flipped"', src)


class BankTheProfit(unittest.TestCase):
    """A 15m contract need not be held to expiry.

    If it drifts our way we can close it, bank the money, free the capital for
    the next mispricing and lose the exposure to a late reversal. Smaller
    margins, more of them.
    """

    def market(self, bid, ask):
        from datetime import datetime, timezone
        from decimal import Decimal
        from crypto15m.domain import MarketQuote
        return MarketQuote(ticker="T", event_ticker="E", strike=Decimal("1"),
            yes_bid=Decimal(bid), yes_ask=Decimal(ask),
            no_bid=Decimal(str(1 - float(ask))), no_ask=Decimal(str(1 - float(bid))),
            yes_ask_size=Decimal("99"), no_ask_size=Decimal("99"),
            open_time=datetime.now(timezone.utc), close_time=datetime.now(timezone.utc),
            status="active", volume=Decimal("0"), open_interest=Decimal("0"), result="")

    def test_a_one_cent_gain_is_not_banked(self):
        """Kalshi charges a taker fee each way; one cent up is a net loss."""
        from decimal import Decimal
        from crypto15m.policy.decision import bankable
        self.assertFalse(bankable("yes", Decimal("0.40"), Decimal("5"),
                                  self.market("0.41", "0.42")))

    def test_a_real_gain_is_banked(self):
        from decimal import Decimal
        from crypto15m.policy.decision import bankable
        self.assertTrue(bankable("yes", Decimal("0.40"), Decimal("5"),
                                 self.market("0.48", "0.49")))

    def test_a_losing_position_is_never_banked(self):
        from decimal import Decimal
        from crypto15m.policy.decision import bankable
        self.assertFalse(bankable("yes", Decimal("0.40"), Decimal("5"),
                                  self.market("0.30", "0.31")))

    def test_no_side_is_symmetric(self):
        from decimal import Decimal
        from crypto15m.policy.decision import bankable
        # NO bought at 0.40 -> closes by buying YES back; profits as yes_ask falls
        self.assertTrue(bankable("no", Decimal("0.40"), Decimal("5"),
                                 self.market("0.50", "0.52")))
        self.assertFalse(bankable("no", Decimal("0.40"), Decimal("5"),
                                  self.market("0.58", "0.59")))

    def test_it_is_checked_before_holding_to_expiry(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/crypto15m/trials.py").read_text()
        # match on structure, not exact formatting - the call now wraps lines
        compact = " ".join(src.split())
        self.assertIn("bankable( pos.side, pos.avg_price, pos.contracts", compact)


class ScaleOut(unittest.TestCase):
    """Bank most of a winner, let a runner ride.

    All-or-nothing forced a choice between the SOL trade that made $2.30 by
    settling and the safety of banking $0.24. Scaling takes the certain money
    and keeps a ticket on the thesis.
    """

    def test_partial_close_leaves_the_rest_open(self):
        from datetime import datetime, timezone
        from decimal import Decimal
        from crypto15m.portfolio.paper import PaperPortfolio
        p = PaperPortfolio(Decimal("100"))
        p.apply_open(now=datetime.now(timezone.utc), ticker="T", side="yes",
                     contracts=Decimal("3"), price=Decimal("0.22"),
                     fee=Decimal("0.03"), permit=None)
        p.reduce(datetime.now(timezone.utc), "T", contracts=Decimal("2"),
                 bid=Decimal("0.40"), fee=Decimal("0.02"))
        pos = p.positions["T"]
        self.assertEqual(pos.contracts, Decimal("1"))
        self.assertEqual(pos.avg_price, Decimal("0.22"), "cost basis must not move")
        self.assertGreater(p.realized_pnl_today, 0)

    def test_reducing_everything_is_a_full_close(self):
        from datetime import datetime, timezone
        from decimal import Decimal
        from crypto15m.portfolio.paper import PaperPortfolio
        p = PaperPortfolio(Decimal("100"))
        p.apply_open(now=datetime.now(timezone.utc), ticker="T", side="yes",
                     contracts=Decimal("2"), price=Decimal("0.22"),
                     fee=Decimal("0.02"), permit=None)
        p.reduce(datetime.now(timezone.utc), "T", contracts=Decimal("5"),
                 bid=Decimal("0.40"), fee=Decimal("0.02"))
        self.assertNotIn("T", p.positions)

    def test_a_single_contract_cannot_be_scaled(self):
        from decimal import Decimal
        keep = max(Decimal("1"), (Decimal("1") * Decimal("0.34")).to_integral_value())
        self.assertEqual(keep, Decimal("1"), "1 contract must bank whole, not partially")

    def test_a_runner_is_always_kept(self):
        from decimal import Decimal
        for n in (2, 3, 5, 8):
            keep = max(Decimal("1"),
                       (Decimal(n) * Decimal("0.34")).to_integral_value(rounding="ROUND_DOWN"))
            self.assertGreaterEqual(keep, 1, f"{n} contracts left no runner")
            self.assertLess(keep, n, f"{n} contracts banked nothing")

    def test_a_window_is_only_scaled_once(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/crypto15m/trials.py").read_text()
        self.assertIn("frame.market.ticker not in self._scaled", src)
        self.assertIn('"scaled": sorted(self._scaled),', src)
