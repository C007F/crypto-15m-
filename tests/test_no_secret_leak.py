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
