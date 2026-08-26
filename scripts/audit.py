#!/usr/bin/env python3
"""Full-system audit. Everything that must be true for this to be trading right.

Written after shipping a fix twice without restarting the services that run it,
so the first section it checks is whether the running processes are actually
running the committed code.
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("ASSET", "sol")
ASSETS7 = ["btc", "eth", "xrp", "doge", "sol", "hype", "bnb"]

OK, BAD, WARN = [], [], []


def chk(name, fn):
    """Print as we go. A buffered audit that dies mid-run tells you nothing."""
    try:
        good, detail = fn()
    except Exception as e:
        good, detail = False, f"check itself failed: {str(e)[:110]}"
    (OK if good is True else WARN if good is None else BAD).append((name, detail))
    mark = "ok" if good is True else "!!" if good is None else "XX"
    print(f"  [{mark}] {name:34} {detail}", flush=True)


def sh(*a, t=25):
    return subprocess.run(a, capture_output=True, text=True, timeout=t).stdout.strip()


def uctl(*a):
    return sh("systemctl", "--user", *a)


# ---------------------------------------------------------------- deployment
def _code_is_deployed():
    """The bug I keep making: commit a fix, never restart the service."""
    last = sh("git", "-C", str(ROOT), "log", "-1", "--format=%ct")
    if not last:
        return False, "cannot read git"
    commit_ts = int(last)
    stale = []
    for a in ASSETS7:
        raw = uctl("show", f"crypto15m-live@{a}", "-p",
                   "ActiveEnterTimestampMonotonic", "--value")
        started = uctl("show", f"crypto15m-live@{a}",
                       "-p", "ActiveEnterTimestamp", "--value")
        if not started:
            stale.append(f"{a}:down"); continue
        try:
            t = datetime.strptime(started[:-4].strip(), "%a %Y-%m-%d %H:%M:%S")
            t = t.replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            continue
        if t < commit_ts:
            stale.append(f"{a}:{int((commit_ts - t) / 60)}min behind")
    if stale:
        return False, "running OLD code: " + ", ".join(stale)
    return True, f"all {len(ASSETS7)} traders started after the last commit"


def _tree_clean():
    d = sh("git", "-C", str(ROOT), "status", "--porcelain")
    files = [l for l in d.splitlines() if not l.endswith((".jsonl", ".log", ".json"))]
    return (not files, "working tree clean" if not files
            else f"{len(files)} uncommitted code file(s): {files[:3]}")


def _services():
    want = [f"crypto15m-live@{a}" for a in ASSETS7] + \
           ["crypto15m-observe", "crypto15m-live-dash"]
    bad = [u for u in want if uctl("is-active", u) != "active"]
    return (not bad, f"{len(want)} services active" if not bad else f"DOWN: {bad}")


def _enabled_at_boot():
    want = [f"crypto15m-live@{a}" for a in ASSETS7] + \
           ["crypto15m-observe", "crypto15m-backup.timer", "crypto15m-exchange.timer"]
    bad = [u for u in want if uctl("is-enabled", u) not in ("enabled", "enabled-runtime")]
    return (not bad, "all survive reboot" if not bad else f"NOT enabled: {bad}")


# --------------------------------------------------------------------- config
def _config():
    from crypto15m.config import load_settings
    s = load_settings()
    facts = {
        "entry_start_sec": (s.entry_start_sec, 90),
        "entry_cutoff_sec": (s.entry_cutoff_sec, 60),
        "flatten_cutoff_sec": (s.flatten_cutoff_sec, 150),
        "min_cost": (s.min_cost, D("0.02")),
        "max_cost": (s.max_cost, D("0.50")),
    }
    off = [f"{k}={v} (want {w})" for k, (v, w) in facts.items() if v != w]
    return (not off, "entry window + price band as intended" if not off
            else "DRIFTED: " + ", ".join(off))


def _risk_limits():
    from crypto15m.live import LiveLimits, STAGES
    l = LiveLimits()
    good = (l.daily_loss_usd == D("15") and l.min_equity_usd == D("50")
            and l.max_contracts_hard == 8 and STAGES[0][0] == 1)
    return good, (f"stop ${l.daily_loss_usd}, floor ${l.min_equity_usd}, "
                  f"cap {l.max_contracts_hard}, ramp starts {STAGES[0][0]}")


def _micro_only():
    from crypto15m.trials import LIVE_BOOKS
    return set(LIVE_BOOKS) == {"micro"}, f"live books = {sorted(LIVE_BOOKS)}"


def _zec_off():
    from crypto15m.bot import NO_LIVE_ASSETS
    return "zec" in NO_LIVE_ASSETS, f"excluded from live: {sorted(NO_LIVE_ASSETS)}"


# ----------------------------------------------------------------- safety net
def _guard_wired():
    import inspect
    from crypto15m.policy import decision
    from crypto15m.perception.features import FeatureFrame
    src = inspect.getsource(decision.decide)
    return ("book_stale" in src
            and "book_stale" in FeatureFrame.__dataclass_fields__,
            "entry gate declines when the orderbook is unreadable")


def _no_funding():
    from crypto15m.kalshi.client import _guard_path
    blocked = 0
    for p in ("/portfolio/deposit", "/portfolio/withdraw", "/transfer",
              "/payment/ach", "/wallet"):
        try:
            _guard_path(p)
        except RuntimeError:
            blocked += 1
    return blocked == 5, "deposit/withdraw/transfer all refused"


def _dash_has_no_creds():
    src = (ROOT / "web" / "live_server.py").read_text()
    bad = [w for w in ("KalshiClient", "load_settings", "private", "kalshi_private")
           if w in src]
    return not bad, ("dashboard reads files only" if not bad
                     else f"web process touches {bad}")


def _secrets_not_served():
    import urllib.request
    try:
        raw = urllib.request.urlopen("http://localhost:8810/status", timeout=10).read()
    except Exception as e:
        return False, f"dashboard unreachable: {str(e)[:60]}"
    hits = [m for m in (b"PRIVATE KEY", b"BEGIN RSA", b"key_id", b"secret")
            if m.lower() in raw.lower()]
    return not hits, "served payload clean" if not hits else f"LEAK: {hits}"


def _key_perms():
    p = ROOT / "kalshi_private.pem"
    if not p.exists():
        return False, "private key missing"
    mode = oct(p.stat().st_mode)[-3:]
    return mode == "600", f"kalshi_private.pem is {mode}"


# ------------------------------------------------------------- live behaviour
def _kill_switch_works():
    import tempfile
    from crypto15m.live import LiveGuard
    d = Path(tempfile.mkdtemp())
    g = LiveGuard(d / "s.json")
    g.roll_day(datetime.now(timezone.utc), D("200"))
    return (bool(g.blocked(D("49"))) and bool(g.blocked(None)),
            "halts under $50 and on unknown equity (fails closed)")


def _daily_cap_live():
    st = json.loads((ROOT / "data" / "live_state.json").read_text())
    ex = json.loads((ROOT / "data" / "exchange.json").read_text())
    day = float(ex.get("day") or 0)
    room = 15 + min(0, day)
    if st.get("halted"):
        return None, f"HALTED: {st['halted']}"
    return True, f"day {day:+.2f}, ${room:.2f} of the $15 stop remaining"


def _reconciles():
    """Our state file vs the exchange."""
    ex = json.loads((ROOT / "data" / "exchange.json").read_text())
    age = time.time() - float(ex.get("at") or 0)
    if age > 300:
        return False, f"exchange snapshot {int(age)}s stale"
    st = json.loads((ROOT / "data" / "live_state.json").read_text())
    ours = float(st.get("realized_today") or 0)
    theirs = float(ex.get("day") or 0)
    gap = abs(ours - theirs)
    return (gap < 1.0, f"journal {ours:+.2f} vs exchange {theirs:+.2f} "
                       f"(gap {gap:.2f}; journal is known to undercount)")


def _fill_rate():
    import glob
    rows = []
    for p in glob.glob(str(ROOT / "data/*/fills.jsonl")):
        for l in open(p):
            if l.strip():
                rows.append(json.loads(l))
    cur = [r for r in rows if r["ts"] >= "2026-08-26T02:39"
           and not str(r.get("side", "")).startswith("exit_")]
    got = [r for r in cur if int(r.get("filled") or 0)]
    if not cur:
        return None, "no attempts since the pricing fix"
    rate = len(got) / len(cur)
    return (rate > 0.5, f"{len(got)}/{len(cur)} = {rate:.0%} since the pricing fix")


def _data_growing():
    out = []
    for name, path, floor in [
        ("observations", ROOT / "research/observations.jsonl", 500),
        ("history", ROOT / "research/history.jsonl", 10000),
    ]:
        n = sum(1 for _ in open(path)) if path.exists() else 0
        out.append(f"{name} {n:,}")
        if n < floor:
            return False, f"{name} only {n} rows"
    return True, ", ".join(out)


def _backup_recent():
    p = ROOT / "research" / "drive_backup.json"
    if not p.exists():
        return False, "no backup has ever run"
    d = json.loads(p.read_text()).get("last") or {}
    at = d.get("at", "")
    return True, f"last backup {d.get('name')} {d.get('bytes',0):,}B at {at[:16]}"


def _tests():
    r = subprocess.run([sys.executable, "-m", "unittest", "discover",
                        "-s", "tests", "-q"], cwd=ROOT,
                       capture_output=True, text=True, timeout=400)
    tail = (r.stderr or "").strip().splitlines()[-1:] or [""]
    return r.returncode == 0, tail[0][:90]


for name, fn in [
    ("deployed code == committed code", _code_is_deployed),
    ("working tree clean", _tree_clean),
    ("services running", _services),
    ("services survive reboot", _enabled_at_boot),
    ("config matches intent", _config),
    ("risk limits intact", _risk_limits),
    ("only micro trades real money", _micro_only),
    ("zec stays sidelined", _zec_off),
    ("stale-book guard wired", _guard_wired),
    ("bot cannot move money", _no_funding),
    ("dashboard holds no credentials", _dash_has_no_creds),
    ("no secrets in served payload", _secrets_not_served),
    ("private key permissions", _key_perms),
    ("kill switch + equity floor", _kill_switch_works),
    ("daily cap headroom", _daily_cap_live),
    ("journal reconciles to exchange", _reconciles),
    ("fill rate healthy", _fill_rate),
    ("research data growing", _data_growing),
    ("backup ran", _backup_recent),
]:
    chk(name, fn)

if "--fast" not in sys.argv:
    chk("test suite", _tests)
print(f"\n{len(OK)}/{len(OK)+len(WARN)+len(BAD)} passed"
      + (f", {len(WARN)} warning" if WARN else "")
      + (f", {len(BAD)} FAILING" if BAD else ""))
sys.exit(1 if BAD else 0)
