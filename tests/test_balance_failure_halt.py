"""A failed balance read must skip the tick, never liquidate the book.

Memo 006 part 3a defined bot.py's module logger, which fixed a crash handler
that crashed. It also removed the NameError that had been ABORTING the tick,
and that abort was the only thing standing between a transient
GET /portfolio/balance failure and this chain:

    eq = None
      -> guard.blocked(None) == "equity unknown - refusing to trade blind"
      -> book.portfolio.flatten_all(now, taker_fee)
      -> PaperPortfolio.flatten() -> del self.positions[ticker]

flatten_all is the paper portfolio's BOOKKEEPING flatten. Unlike every exit in
trials.py it never calls _live_close, so no order reaches Kalshi: the ledger
books a fill at the bid and forgets the position while the real contracts stay
open until settlement. 60 of these aborts are in the live logs.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "crypto15m"


def _strip_comments(text: str) -> str:
    """Code only. A test that greps source must not match its own prose."""
    return "\n".join(l for l in text.splitlines()
                     if not l.lstrip().startswith("#"))


def _live_gate_source() -> str:
    """The balance-fetch block inside Mind.step(), comments removed."""
    src = (SRC / "bot.py").read_text()
    start = src.index("bal = self.client.get_balance()")
    end = src.index("frame = self.features(now)", start)
    return _strip_comments(src[start:end])


class BalanceFailureSkipsTheTick(unittest.TestCase):
    def test_handler_returns_instead_of_falling_through(self):
        """The regression: the except block must not fall through to blocked()."""
        block = _live_gate_source()
        handler = block[block.index("except Exception"):]
        # Everything up to the next dedented statement is the handler body.
        body = handler[:handler.index("\n            if eq is not None")]
        self.assertIn("return", body,
                      "balance-fetch failure must skip the tick, not fall "
                      "through to blocked(None) -> flatten_all()")

    def test_handler_does_not_log_a_stack_every_tick(self):
        """This runs at 2Hz x 7 processes during exactly the storms that break it."""
        block = _live_gate_source()
        handler = block[block.index("except Exception"):]
        body = handler[:handler.index("\n            if eq is not None")]
        self.assertNotIn("exc_info", body,
                         "a per-tick traceback is ~0.65 GB/day across the fleet")

    def test_flatten_all_still_does_not_reach_the_exchange(self):
        """Documents WHY the tick must be skipped. If this ever fails, the
        premise changed and the handler above can be revisited."""
        paper = (SRC / "portfolio" / "paper.py").read_text()
        tree = ast.parse(paper)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "flatten_all")
        calls = {n.func.attr for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        self.assertNotIn("_live_close", calls)
        self.assertIn("flatten", calls)

    def test_equity_unknown_is_still_a_halt_reason(self):
        """We skip the tick BEFORE blocked() is consulted; the reason itself
        must stay, so a genuinely unknown equity still refuses to trade."""
        self.assertIn("equity unknown", (SRC / "live.py").read_text())


class ShouldBuildIsWiredIntoProduction(unittest.TestCase):
    """The review's highest-value gap: every should_build assertion was made
    against a bare EmpiricalMoves the test drove itself. Reverting the one
    production line back to `.stale` left all 175 tests green."""

    def test_features_gates_the_rebuild_on_should_build(self):
        src = (SRC / "bot.py").read_text()
        start = src.index("def features(")
        body = src[start:src.index("\n    def ", start + 10)]
        self.assertIn("self.history.should_build", body,
                      "Mind.features() must gate on should_build; .stale alone "
                      "reintroduces the every-tick refetch storm")
        self.assertNotIn("if self.history.stale:", body)

    def test_a_failing_build_is_attempted_once_not_every_tick(self):
        """Drive the real predicate the way features() does."""
        import tempfile
        import time

        from crypto15m.perception.history import EmpiricalMoves

        class Boom:
            calls = 0

            def get(self, *a, **k):
                Boom.calls += 1
                raise RuntimeError("429")

        with tempfile.TemporaryDirectory() as d:
            em = EmpiricalMoves("TEST-USD")
            em.CACHE_DIR = Path(d)
            for _ in range(400):                 # ~3 min of ticks at 0.5s
                if em.should_build:              # the exact production guard
                    em.build(Boom())
            self.assertEqual(Boom.calls, 1)
            self.assertGreater(em._next_try, time.time())


class LadderResetIsActuallyAsserted(unittest.TestCase):
    """Replaces test_success_clears_the_ladder, which contained
    `assertTrue(x or True)` and then hand-performed the very assignments it
    claimed to verify."""

    def test_success_clears_fails_and_next_try(self):
        import tempfile

        from crypto15m.perception.history import EmpiricalMoves

        rows = [[i * 60, 0, 0, 0, 100.0 + (i % 7)] for i in range(700)]

        class Ok:
            def get(self, *a, **k):
                class R:
                    status_code = 200

                    @staticmethod
                    def raise_for_status():
                        return None

                    @staticmethod
                    def json():
                        return rows
                return R()

        with tempfile.TemporaryDirectory() as d:
            em = EmpiricalMoves("TEST-USD")
            em.CACHE_DIR = Path(d)
            em._note_failure()
            em._note_failure()
            self.assertEqual(em._fails, 2)
            self.assertGreater(em._next_try, 0.0)

            em._next_try = 0.0                   # let the window elapse
            self.assertTrue(em.build(Ok()), "synthetic candles should build")
            # The point of the test: success RESETS the ladder.
            self.assertEqual(em._fails, 0)
            self.assertEqual(em._next_try, 0.0)
            self.assertFalse(em.should_build)


class HistOkReflectsTheRealPredicate(unittest.TestCase):
    """The old test was `assertIn("hist_ok", source)`, which the review showed
    passes with the value hardcoded to True."""

    def _payload_value(self, path: Path, kind: str) -> str:
        src = path.read_text()
        i = src.index('"hist_ok"')
        return src[i:src.index("\n", i)]

    def test_bot_hist_ok_is_computed_not_constant(self):
        line = self._payload_value(SRC / "bot.py", "prediction")
        self.assertIn("frame.hist_p_yes is not None", line)
        self.assertNotIn("True", line)

    def test_trials_hist_ok_is_computed_not_constant(self):
        line = self._payload_value(SRC / "trials.py", "fill")
        self.assertIn("frame.hist_p_yes is not None", line)
        self.assertNotIn("True", line)


if __name__ == "__main__":
    unittest.main()
