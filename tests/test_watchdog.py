"""Board Memo 004 phase 1: the watchdog pages, it does not kill."""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from crypto15m.kalshi.stream import synthesize_window_tickers

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import watchdog as wd  # noqa: E402

ET = ZoneInfo("America/New_York")


class TickerClose(unittest.TestCase):
    def test_known_ticker(self):
        close = wd.ticker_close_utc("KXBTC15M-26AUG281300-00")
        self.assertIsNotNone(close)
        et = close.astimezone(ET)
        self.assertEqual((et.year, et.month, et.day, et.hour, et.minute),
                         (2026, 8, 28, 13, 0))

    def test_garbage(self):
        self.assertIsNone(wd.ticker_close_utc("nope"))


class LogScan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = Path(self.tmp.name) / "live.log"

    def _write(self, lines, start=None):
        start = start or time.time() - 60
        out = []
        ct = datetime.fromtimestamp(start, wd.CT)
        for i, kind in enumerate(lines):
            ts = (ct + timedelta(seconds=i)).strftime("%Y-%m-%d %H:%M:%S")
            if kind == "ok":
                out.append(f"KXBTC15M-26AUG281300-00 8m0s  BTC 1 vs 1")
            elif kind == "429":
                out.append(f"{ts} WARNING crypto15m.kalshi.client: orderbook read failed for X (429 Client Error)")
            elif kind == "stale":
                out.append(f"{ts} INFO crypto15m.policy.decision: declined: book_stale n=1 ticker=X")
            elif kind == "fail":
                out.append("LIVE step failed (HTTPError: 429 Client Error: Too Many Requests)")
        self.p.write_text("\n".join(out) + "\n")

    def test_all_clean(self):
        self._write(["ok"] * 30)
        ticks, bad = wd.scan_log(self.p, time.time())
        self.assertGreaterEqual(ticks, 30)
        self.assertEqual(bad, 0)

    def test_storm_over_20_percent(self):
        self._write(["ok"] * 10 + ["429"] * 10)
        ticks, bad = wd.scan_log(self.p, time.time())
        self.assertGreater(bad / max(ticks, 1), 0.20)


class Triggers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for a in wd.LIVE_ASSETS:
            (self.root / a).mkdir()
            (self.root / a / "live.log").write_text("KXBTC15M-TEST 1m\n")
            (self.root / a / "fills.jsonl").write_text(
                json.dumps({"ts": datetime.now(timezone.utc).isoformat(),
                            "ticker": "KXBTC15M-26AUG281300-00",
                            "filled": 1, "side": "no"}) + "\n"
            )
        (self.root / "books").mkdir()

    def test_stream_down(self):
        alerts = wd.collect_alerts(time.time(), data_dir=self.root,
                                   stream_is_active=False)
        self.assertTrue(any(a["id"] == "d:stream" for a in alerts))

    def test_stale_book_while_stream_up(self):
        tickers = synthesize_window_tickers()
        now = time.time()
        for series, ticker in tickers.items():
            f = self.root / "books" / f"{ticker}.json"
            f.write_text("{}")
            os.utime(f, (now - 240, now - 240))
        alerts = wd.collect_alerts(now, data_dir=self.root, stream_is_active=True)
        self.assertTrue(any(a["id"].startswith("b:") for a in alerts))

    def test_fresh_book_is_quiet(self):
        tickers = synthesize_window_tickers()
        for series, ticker in tickers.items():
            (self.root / "books" / f"{ticker}.json").write_text("{}")
        alerts = wd.collect_alerts(time.time(), data_dir=self.root,
                                   stream_is_active=True)
        self.assertFalse(any(a["id"].startswith("b:") for a in alerts))
        self.assertFalse(any(a["id"] == "d:stream" for a in alerts))

    def test_unsettled_overdue(self):
        old = "KXDOGE15M-26AUG280800-00"   # 08:00 ET Aug 28, long closed
        (self.root / "doge" / "fills.jsonl").write_text(
            json.dumps({"ts": "2026-08-28T13:00:00+00:00",
                        "ticker": old, "filled": 1, "side": "no"}) + "\n"
        )
        found = wd.unsettled_overdue(self.root, datetime.now(timezone.utc))
        self.assertTrue(any(u["ticker"] == old for u in found))

    def test_phase2_is_off(self):
        self.assertIs(wd.PHASE2_AUTOKILL, False)
        src = Path(wd.__file__).read_text()
        self.assertNotIn("kill_path", src)
        # May mention LIVE_KILL in comments as what we do not touch.
        writes = [ln for ln in src.splitlines()
                  if "LIVE_KILL" in ln and ("write_text" in ln or "open(" in ln)]
        self.assertEqual(writes, [])


if __name__ == "__main__":
    unittest.main()
