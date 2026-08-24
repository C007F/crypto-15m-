#!/usr/bin/env python3
"""Pre-flight checks. Run before arming live. Places no orders.

Each check is a thing that, if wrong, loses money silently.
"""
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "src")

from crypto15m.config import load_settings                       # noqa: E402
from crypto15m.kalshi.client import KalshiClient                 # noqa: E402
from crypto15m.live import LiveGuard, LiveLimits                 # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, fn):
    try:
        ok, detail = fn()
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    results.append((PASS if ok else FAIL, name, detail))
    print(f"  [{PASS if ok else FAIL}] {name:44} {detail}")


s = load_settings()
print(f"\nPRE-FLIGHT  asset={s.asset.key}  mode={s.mode}  env={s.kalshi_env}\n")

# ---------------------------------------------------------------- credentials
def _auth():
    c = KalshiClient(s)
    bal = c.get_balance()
    d = bal.get("balance_dollars")
    return (d is not None), f"balance_dollars={d}"
check("Kalshi auth + balance", _auth)

def _units():
    c = KalshiClient(s)
    bal = c.get_balance()
    cents, dollars = bal.get("balance"), Decimal(str(bal.get("balance_dollars")))
    return (abs(Decimal(cents) / 100 - dollars) < Decimal("0.01"),
            f"{cents} cents == ${dollars}")
check("balance units agree (cents vs dollars)", _units)

# ------------------------------------------------------------- strategy parity
def _parity():
    out = subprocess.run(
        ["git", "log", "--oneline", "--since=2026-08-22",
         "--", "src/crypto15m/experts", "src/crypto15m/policy", "src/crypto15m/meta"],
        capture_output=True, text=True)
    n = len([l for l in out.stdout.splitlines() if l.strip()])
    return n == 0, ("decision logic untouched since paper"
                    if n == 0 else f"{n} commits changed decision logic")
check("strategy identical to the paper run", _parity)

# --------------------------------------------------------------- loss limiter
def _loss_cap():
    d = Path(tempfile.mkdtemp())
    g = LiveGuard(d / "live_state.json", LiveLimits(daily_loss_usd=Decimal("15")))
    g.roll_day(datetime.now(timezone.utc), Decimal("200"))
    if g.blocked(Decimal("200")):
        return False, "blocked before any loss"
    g.record_close(Decimal("-14.99"))
    if g.blocked(Decimal("200")):
        return False, "tripped early at -14.99"
    g.record_close(Decimal("-0.02"))
    return bool(g.blocked(Decimal("200"))), f"trips at -$15.01: {g.blocked(Decimal('200'))}"
check("daily loss cap trips at the limit", _loss_cap)

def _cap_shared():
    d = Path(tempfile.mkdtemp())
    a = LiveGuard(d / "live_state.json"); a.roll_day(datetime.now(timezone.utc), Decimal("200"))
    b = LiveGuard(d / "live_state.json")
    a.record_close(Decimal("-16"))
    return bool(b.blocked(Decimal("200"))), "one coin's loss halts the others"
check("loss cap is account-wide, not per coin", _cap_shared)

def _wired():
    src = Path("src/crypto15m/trials.py").read_text()
    return ("self.guard.record_close(pnl)" in src), "record_close called from _review()"
check("loss counter is actually fed by exits", _wired)

# ------------------------------------------------------------------- switches
def _kill():
    d = Path(tempfile.mkdtemp())
    g = LiveGuard(d / "live_state.json"); g.roll_day(datetime.now(timezone.utc), Decimal("200"))
    (d / "LIVE_KILL").touch()
    return bool(g.blocked(Decimal("200"))), "account kill file halts"
check("LIVE_KILL stops trading", _kill)

def _floor():
    d = Path(tempfile.mkdtemp())
    g = LiveGuard(d / "live_state.json"); g.roll_day(datetime.now(timezone.utc), Decimal("200"))
    return bool(g.blocked(Decimal("49"))), "halts under $50"
check("equity floor holds", _floor)

def _blind():
    d = Path(tempfile.mkdtemp())
    g = LiveGuard(d / "live_state.json"); g.roll_day(datetime.now(timezone.utc), Decimal("200"))
    return bool(g.blocked(None)), "unknown equity halts (fails closed)"
check("refuses to trade on unknown equity", _blind)

def _size():
    d = Path(tempfile.mkdtemp())
    g = LiveGuard(d / "live_state.json")
    start = g.max_contracts
    for _ in range(400):
        g.note_order()
    return (start == 1 and g.max_contracts <= g.limits.max_contracts_hard,
            f"starts at {start}, ceiling {g.max_contracts}")
check("sizing starts at 1 and respects the ceiling", _size)

# ---------------------------------------------------------------- fill honesty
def _fill_honesty():
    src = Path("src/crypto15m/trials.py").read_text()
    return ("filled, avg = parse_fill(resp)" in src
            and "if filled < 1:" in src
            and 'journal.log("no_fill"' in src), "unfilled orders book nothing"
check("HTTP 200 is not treated as filled", _fill_honesty)

def _close_real():
    src = Path("src/crypto15m/trials.py").read_text()
    return (src.count("_live_close") >= 3), "flatten sends a real sell first"
check("live cuts reach the exchange", _close_real)

# -------------------------------------------------------------- money movement
def _no_funding():
    from crypto15m.kalshi.client import _guard_path
    blocked = 0
    for bad in ("/portfolio/deposit", "/wallet/withdraw", "/portfolio/transfers",
                "/portfolio/ach", "/payment/new"):
        try:
            _guard_path(bad)
        except RuntimeError:
            blocked += 1
    return blocked == 5, "deposit/withdraw/transfer endpoints all refused"
check("bot cannot move money", _no_funding)

def _allowlist():
    from crypto15m.kalshi.client import ALLOWED_PREFIXES
    return (len(ALLOWED_PREFIXES) <= 8,
            f"{len(ALLOWED_PREFIXES)} endpoints reachable, read+orders only")
check("endpoint allowlist is narrow", _allowlist)

# ------------------------------------------------------------- live scope
def _micro_only():
    from crypto15m.trials import BOOKS, LIVE_BOOKS
    return (set(LIVE_BOOKS) == {"micro"},
            f"live={sorted(LIVE_BOOKS)}  paper={[b.book_id for b in BOOKS]}")
check("only micro trades real money", _micro_only)

# ------------------------------------------------------------- secret hygiene
def _no_leak():
    import subprocess
    r = subprocess.run([sys.executable, "-m", "unittest",
                        "tests.test_no_secret_leak", "-q"],
                       capture_output=True, text=True)
    return r.returncode == 0, "web payloads and pages scanned clean"
check("no secrets reachable from the web", _no_leak)

def _perms():
    bad = [f for f in ("kalshi_private.pem", ".env")
           if Path(f).exists() and (Path(f).stat().st_mode & 0o077)]
    return not bad, f"secrets are 0600 ({'ok' if not bad else bad})"
check("secret files are not world/group readable", _perms)

def _dash_auth():
    import subprocess
    url = "http://127.0.0.1/db589b7806aff1205013854472d697e5/crypto/api/status?asset=board"
    r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", url],
                       capture_output=True, text=True)
    return r.stdout.strip() == "401", f"unauthenticated dashboard -> {r.stdout.strip()}"
check("dashboards require a password", _dash_auth)

# ------------------------------------------------------------------- location
def _local():
    import socket
    return socket.gethostname() == "Chester", f"host={socket.gethostname()}"
check("running on Chester", _local)

bad = [r for r in results if r[0] == FAIL]
print(f"\n{len(results)-len(bad)}/{len(results)} passed")
if bad:
    print("\nDO NOT ARM. Failing:")
    for _, name, detail in bad:
        print(f"  - {name}: {detail}")
sys.exit(1 if bad else 0)
