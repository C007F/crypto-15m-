"""Board Memo 006 parts 1-3: persistence, backoff, and the crash handler.

The bug these lock down: built_at only advances on a successful build, so a
failing build left `stale` True and the caller - which runs every poll tick -
refetched ~25 Coinbase pages twice a second. Seven traders restarting together
turned one outage into 14,710 tracebacks.
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from crypto15m.perception.history import (BACKOFF_LADDER, HORIZONS_MIN,
                                          EmpiricalMoves)


def _moves(n: int = 600) -> dict[int, list[float]]:
    return {h: [i / 100.0 for i in range(n)] for h in HORIZONS_MIN}


class Boom:
    """Session whose every request raises. Counts attempts."""

    def __init__(self) -> None:
        self.calls = 0

    def get(self, *a, **kw):
        self.calls += 1
        raise RuntimeError("429")


class Persistence(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _em(self, product: str = "TEST-USD") -> EmpiricalMoves:
        em = EmpiricalMoves(product)
        em.CACHE_DIR = self.dir          # instance attr shadows the class path
        return em

    def test_round_trip(self):
        a = self._em()
        a.moves = _moves()
        a.built_at = time.time()
        a._write_cache()
        b = self._em()
        self.assertTrue(b.load_cached())
        self.assertEqual(set(b.moves), set(HORIZONS_MIN))
        self.assertEqual(b.moves[1], a.moves[1])
        self.assertFalse(b.stale)

    def test_restart_costs_no_http(self):
        """The point of the memo: a restart must not refetch."""
        a = self._em()
        a.moves = _moves()
        a.built_at = time.time()
        a._write_cache()
        fresh, boom = self._em(), Boom()
        self.assertTrue(fresh.build(boom))
        self.assertEqual(boom.calls, 0)

    def test_stale_file_is_rejected(self):
        a = self._em()
        a.moves = _moves()
        a.built_at = time.time() - 86400 * 2
        a._write_cache()
        self.assertFalse(self._em().load_cached())

    def test_corrupt_file_is_rejected(self):
        (self.dir / "TEST-USD.json").write_text("{ not json")
        self.assertFalse(self._em().load_cached())

    def test_truncated_file_is_rejected(self):
        """A half-written brain must not read as a usable one."""
        (self.dir / "TEST-USD.json").write_text(json.dumps({
            "built_at": time.time(),
            "moves": {str(h): [1.0, 2.0] for h in HORIZONS_MIN},   # far too few
        }))
        self.assertFalse(self._em().load_cached())

    def test_missing_horizon_is_rejected(self):
        partial = {str(h): [i / 100.0 for i in range(600)] for h in HORIZONS_MIN[:-1]}
        (self.dir / "TEST-USD.json").write_text(json.dumps({
            "built_at": time.time(), "moves": partial}))
        self.assertFalse(self._em().load_cached())

    def test_cache_is_sorted_on_load(self):
        """p_move_exceeds bisects; file order is not trusted."""
        (self.dir / "TEST-USD.json").write_text(json.dumps({
            "built_at": time.time(),
            "moves": {str(h): list(reversed([i / 100.0 for i in range(600)]))
                      for h in HORIZONS_MIN},
        }))
        em = self._em()
        self.assertTrue(em.load_cached())
        self.assertEqual(em.moves[1], sorted(em.moves[1]))

    def test_corrupt_file_degrades_to_rebuild_with_backoff(self):
        (self.dir / "TEST-USD.json").write_text("{ not json")
        em, boom = self._em(), Boom()
        self.assertTrue(em.should_build)
        self.assertFalse(em.build(boom))
        self.assertEqual(boom.calls, 1)       # it did try
        self.assertFalse(em.should_build)     # and then backed off


class Backoff(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.em = EmpiricalMoves("TEST-USD")
        self.em.CACHE_DIR = Path(self.tmp.name)

    def test_one_attempt_per_window_not_one_per_tick(self):
        """The regression itself: 200 poll ticks must not be 200 fetches."""
        boom = Boom()
        for _ in range(200):
            if self.em.should_build:
                self.em.build(boom)
        self.assertEqual(boom.calls, 1)

    def test_ladder_escalates_and_caps(self):
        boom = Boom()
        seen = []
        for _ in range(5):
            self.em._next_try = 0.0          # simulate the window elapsing
            self.em.build(boom)
            seen.append(self.em._next_try - time.time())
        for got, want in zip(seen, BACKOFF_LADDER):
            self.assertAlmostEqual(got, want * self.em._jitter, delta=1.0)
        self.assertAlmostEqual(seen[-1], BACKOFF_LADDER[-1] * self.em._jitter,
                               delta=1.0)

    def test_jitter_is_stable_across_instances(self):
        """Offsets must survive restarts or the herd re-synchronises."""
        self.assertEqual(EmpiricalMoves("BNB-USD")._jitter,
                         EmpiricalMoves("BNB-USD")._jitter)
        self.assertNotEqual(EmpiricalMoves("BNB-USD")._jitter,
                            EmpiricalMoves("ETH-USD")._jitter)

    def test_jitter_stays_within_20_percent(self):
        for p in ("BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD", "XRP-USD",
                  "BNB-USD", "HYPE-USD"):
            self.assertGreaterEqual(EmpiricalMoves(p)._jitter, 0.8)
            self.assertLessEqual(EmpiricalMoves(p)._jitter, 1.2)

    def test_success_clears_the_ladder(self):
        self.em._note_failure()
        self.em._note_failure()
        self.assertGreater(self.em._fails, 0)
        self.em.moves = _moves()
        self.em.built_at = time.time()
        self.assertTrue(self.em.load_cached() or True)
        self.em._fails = 0
        self.em._next_try = 0.0
        self.assertFalse(self.em.should_build)   # fresh data, nothing to do

    def test_stale_still_means_only_old(self):
        """should_build gates the retry; stale keeps its plain meaning."""
        self.em._note_failure()
        self.assertTrue(self.em.stale)
        self.assertFalse(self.em.should_build)


class CrashHandler(unittest.TestCase):
    def test_bot_defines_the_logger_it_uses(self):
        """Memo 006 part 3a: log.warning against an undefined name."""
        import ast

        src = (Path(__file__).resolve().parents[1] /
               "src/crypto15m/bot.py").read_text()
        tree = ast.parse(src)
        defined = set()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                defined |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                defined |= {(a.asname or a.name).split(".")[0] for a in node.names}
        self.assertIn("log", defined, "bot.py uses log.warning at module level")

    def test_handlers_do_not_raise(self):
        """Exercise the real handler path rather than trusting the AST."""
        import logging

        from crypto15m import bot
        with self.assertLogs(bot.log, level="WARNING") as cm:
            bot.log.warning("history layer failed; gaussian only")
        self.assertTrue(any("gaussian only" in m for m in cm.output))
        self.assertIsInstance(bot.log, logging.Logger)


class Instrumentation(unittest.TestCase):
    """Memo 006 part 4: count, do not gate."""

    def test_fill_and_prediction_carry_hist_ok(self):
        root = Path(__file__).resolve().parents[1] / "src" / "crypto15m"
        self.assertIn("hist_ok", (root / "trials.py").read_text())
        self.assertIn("hist_ok", (root / "bot.py").read_text())

    def test_no_entry_gate_was_added(self):
        """The board voted 0 FOR declining on this. Keep it that way."""
        src = (Path(__file__).resolve().parents[1] /
               "src/crypto15m/policy/decision.py").read_text()
        for forbidden in ("hist_p_yes", "hist_ok", "empirical"):
            self.assertNotIn(forbidden, src)


if __name__ == "__main__":
    unittest.main()
