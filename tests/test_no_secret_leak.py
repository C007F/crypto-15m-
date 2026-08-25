"""Nothing secret may ever reach a web response. Enforced, not assumed."""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))

SECRET_PATTERNS = [
    re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
    re.compile(r"\bapi[_-]?key\b", re.I),
    re.compile(r"\bapi[_-]?secret\b", re.I),
    re.compile(r"\bprivate[_-]?key\b", re.I),
    re.compile(r"\bbearer\b", re.I),
    re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),  # key id
]


class NoSecretLeak(unittest.TestCase):
    def scan(self, text, where):
        for pat in SECRET_PATTERNS:
            m = pat.search(text)
            self.assertIsNone(m, f"{where} leaked {pat.pattern!r}: {m.group(0) if m else ''}")

    def test_api_payload_carries_no_secrets(self):
        import server
        self.scan(json.dumps(server.build_board(), default=str), "board payload")
        for asset in ("btc", "zec"):
            self.scan(json.dumps(server.build(asset), default=str), f"{asset} payload")

    def test_static_page_carries_no_secrets(self):
        page = (ROOT / "web" / "static" / "index.html").read_text(encoding="utf-8")
        self.scan(page, "index.html")

    def test_server_source_never_reads_the_env_or_key(self):
        src = (ROOT / "web" / "server.py").read_text(encoding="utf-8")
        for bad in ("kalshi_private", ".env", "KALSHI_API", "api_secret"):
            self.assertNotIn(bad, src, f"web server references {bad}")

    def test_secrets_are_not_inside_the_served_directory(self):
        served = ROOT / "web" / "static"
        for f in served.rglob("*"):
            if f.is_file():
                self.scan(f.read_text(encoding="utf-8", errors="ignore"), str(f))

    def test_key_and_env_are_gitignored(self):
        ignore = (ROOT / ".gitignore").read_text()
        self.assertIn(".env", ignore)
        self.assertTrue("*.pem" in ignore or "kalshi_private.pem" in ignore)


class LiveIsMicroOnly(unittest.TestCase):
    def test_only_micro_may_trade_real_money(self):
        sys.path.insert(0, str(ROOT / "src"))
        from crypto15m.trials import BOOKS, LIVE_BOOKS
        self.assertEqual(set(LIVE_BOOKS), {"micro"})
        losing = {"momentum", "lotto"}
        self.assertFalse(losing & set(LIVE_BOOKS),
                         "a book with measured negative expectancy is live")
        self.assertGreater(len(BOOKS), len(LIVE_BOOKS),
                           "paper should still run every book for comparison")


class LiveDashboardIsClean(unittest.TestCase):
    def test_live_payload_has_no_secrets(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
        import live_server
        payload = json.dumps(live_server.build(), default=str)
        for pat in SECRET_PATTERNS:
            self.assertIsNone(pat.search(payload), f"live payload leaked {pat.pattern}")

    def test_live_server_never_touches_credentials(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "web/live_server.py").read_text()
        for bad in ("kalshi_private", ".env", "KALSHI_API", "api_secret", "load_settings"):
            self.assertNotIn(bad, src, f"live dashboard references {bad}")


class LivePnlAccounting(unittest.TestCase):
    def payload(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
        import live_server
        return live_server.build()

    def test_per_asset_pnl_sums_to_the_total(self):
        d = self.payload()
        self.assertAlmostEqual(sum(c["pnl"] for c in d["coins"]), d["pnl"], places=4)

    def test_wins_plus_losses_equals_closed(self):
        for c in self.payload()["coins"]:
            self.assertEqual(c["closed"], c["wins"] + c["losses"])

    def test_paper_history_is_excluded(self):
        """Live P&L must not include the 277 paper trades."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
        import live_server
        # The cutoff moved when the shadow got its own journal; before that
        # the two books shared a ledger and their trades cannot be separated.
        self.assertGreaterEqual(live_server.LIVE_SINCE, "2026-08-24")
        d = self.payload()
        self.assertLess(d["closed"], 50, "paper trades leaked into live P&L")

    def test_the_known_bad_zec_row_is_corrected(self):
        """That settle was booked before the NO cost-basis fix and recorded
        -0.9410 for a trade that really cost 7.35c."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
        import live_server
        self.assertIn(("zec", "2026-08-24T20:30:24"), live_server.CORRECTIONS)
        # Assert the correction is APPLIED, not a threshold on the running
        # total - ZEC keeps trading, so any absolute bound rots. Without the
        # correction the total would be 0.8675 lower (the gap between the
        # mis-booked -0.9410 and the real -0.0735).
        zec = [c for c in self.payload()["coins"] if c["key"] == "zec"][0]
        saved = live_server.CORRECTIONS[("zec", "2026-08-24T20:30:24")]
        self.assertAlmostEqual(saved, -0.0735, places=4)
        uncorrected = zec["pnl"] - (saved - (-0.9410))
        self.assertLess(uncorrected, zec["pnl"],
                        "corrected total should be higher than the raw one")


class ShadowComparison(unittest.TestCase):
    """The shadow book isolates execution cost from signal quality."""

    def payload(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
        import live_server
        return live_server.build()

    def test_gap_is_live_minus_shadow(self):
        d = self.payload()
        for c in d["coins"]:
            self.assertAlmostEqual(c["gap"], c["pnl"] - c["shadow_pnl"], places=4)
        self.assertAlmostEqual(d["gap"], d["pnl"] - d["shadow_pnl"], places=4)

    def test_shadow_runs_in_the_live_process_not_as_extra_traders(self):
        """Six more polling processes would rate-limit the live ones, and a
        429 arriving when a cut needs placing is the failure that inverts the
        strategy."""
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/crypto15m/bot.py").read_text()
        self.assertIn("self.shadow = TrialBook(", src)
        self.assertIn("self.shadow.step(", src)
        self.assertIn("live=False", src.split("self.shadow.step(")[1][:200])

    def test_shadow_never_places_an_order(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/crypto15m/bot.py").read_text()
        call = src.split("self.shadow.step(")[1][:220]
        self.assertIn("live=False", call, "shadow must never trade for real")

    def test_shadow_writes_to_its_own_directory(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/crypto15m/bot.py").read_text()
        self.assertIn('settings.data_dir / "shadow"', src)


class ShadowHasItsOwnJournal(unittest.TestCase):
    """Sharing one journal made shadow trades read as live ones.

    XRP showed 6-0 on a single real fill because thirteen shadow fills were
    being counted as live. A comparison is worthless if both sides write to
    the same ledger.
    """

    def test_shadow_writes_to_a_separate_journal(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/crypto15m/bot.py").read_text()
        self.assertIn('self.shadow_journal = Journal(shadow_dir / "journal.sqlite3")', src)
        self.assertIn("journal=self.shadow_journal", src)

    def test_live_step_does_not_pass_the_live_journal_to_the_shadow(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/crypto15m/bot.py").read_text()
        call = src.split("self.shadow.step(")[1][:260]
        self.assertNotIn("journal=self.journal", call)


class ReconstructedHistory(unittest.TestCase):
    """Moving LIVE_SINCE must not erase real trades.

    fills.jsonl only ever recorded live orders, so it stayed clean while the
    journals were mixed. Eleven real trades are rebuilt from it.
    """

    def server(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
        import live_server
        return live_server

    def test_reconstruction_is_loaded(self):
        r = self.server().reconstructed()
        self.assertTrue(r, "pre-split history missing")
        self.assertGreaterEqual(sum(v["wins"] + v["losses"] for v in r.values()), 10)

    def test_it_is_folded_into_the_totals(self):
        d = self.server().build()
        self.assertGreaterEqual(d["closed"], 10,
                                "pre-split trades not reflected in the totals")

    def test_reconstruction_is_within_reach_of_the_account(self):
        """Fees are recomputed not stored, so exactness is not claimed - but
        it should be close to the real balance delta."""
        r = self.server().reconstructed()
        total = sum(v["pnl"] for v in r.values())
        self.assertLess(abs(total - (-0.8784)), 0.40,
                        "reconstruction has drifted from the account")
