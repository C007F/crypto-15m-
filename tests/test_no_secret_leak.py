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
        self.assertTrue(live_server.LIVE_SINCE.startswith("2026-08-24"))
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
        zec = [c for c in self.payload()["coins"] if c["key"] == "zec"][0]
        self.assertGreater(zec["pnl"], -0.5, "still using the pre-fix number")
