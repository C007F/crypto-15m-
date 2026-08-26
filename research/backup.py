#!/usr/bin/env python3
"""Back the trading research up to Google Drive, labelled and secret-free.

Reuses the hyrox service account. Uploads ONE dated zip containing the data
that would be expensive or impossible to reconstruct, plus a MANIFEST.md that
says what each file is and how it was produced - a bare zip of jsonl is
useless in six months without that.

Secrets never enter the archive: the private key, .env, and creds are excluded
by name AND the whole archive is scanned for key material before upload.
"""
from __future__ import annotations
import io, json, os, subprocess, sys, zipfile, datetime, hashlib
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path("/home/cam/projects/crypto-15m-bot")
HYROX = Path("/home/cam/projects/hyrox")
CREDS = HYROX / "creds.json"
STATE = REPO / "research" / "drive_backup.json"
TZ = ZoneInfo("America/Chicago")
FOLDER_NAME = os.environ.get("DRIVE_FOLDER_NAME", "chester")
KEEP = int(os.environ.get("DRIVE_KEEP", "21"))
SCOPE = "https://www.googleapis.com/auth/drive"
PREFIX = "crypto15m-"

# Never archive these, whatever else matches.
DENY_NAMES = {"kalshi_private.pem", ".env", "creds.json", "telegram.json"}
DENY_EXT = {".pem", ".key", ".pyc"}
# Tripwire: if any of these appear in archive bytes, abort rather than upload.
SECRET_MARKERS = (b"PRIVATE KEY", b"BEGIN RSA", b"api_key", b"APCA-API")


def collect():
    """(archive_path, source_path) pairs."""
    items = []
    for rel in [
        "research/observations.jsonl",
        "research/history.jsonl",
        "research/drift.jsonl",
        "research/OPEN_QUESTIONS.md",
        "STATUS.md", "ARCHITECTURE.md", "README.md",
        "data/live_state.json",
    ]:
        p = REPO / rel
        if p.exists():
            items.append((rel, p))
    for d in sorted((REPO / "data").iterdir()):
        if not d.is_dir():
            continue
        for f in ("fills.jsonl", "lessons_live.jsonl", "lessons.jsonl",
                  "ladder.jsonl", "status.json"):
            p = d / f
            if p.exists():
                items.append((f"data/{d.name}/{f}", p))
    return [(a, b) for a, b in items
            if b.name not in DENY_NAMES and b.suffix not in DENY_EXT]


def api_snapshot():
    """Authoritative account history straight from Kalshi."""
    sys.path.insert(0, str(REPO / "src"))
    os.environ.setdefault("ASSET", "sol")
    from crypto15m.config import load_settings
    from crypto15m.kalshi.client import KalshiClient
    c = KalshiClient(load_settings())
    out = {}
    for name, path, params in [
        ("balance", "/portfolio/balance", None),
        ("settlements", "/portfolio/settlements", {"limit": 200}),
        ("fills", "/portfolio/fills", {"limit": 200}),
        ("positions", "/portfolio/positions", None),
    ]:
        try:
            out[name] = c.auth_get(path, params)
        except Exception as e:
            out[name] = {"error": str(e)[:200]}
    return out


def git_state():
    def sh(*a):
        try:
            return subprocess.run(a, cwd=REPO, capture_output=True,
                                  text=True, timeout=20).stdout.strip()
        except Exception:
            return ""
    return {"commit": sh("git", "rev-parse", "HEAD"),
            "subject": sh("git", "log", "-1", "--format=%s"),
            "log": sh("git", "log", "-30", "--format=%h %ad %s",
                      "--date=format:%Y-%m-%d %H:%M")}


def safe_config():
    """The knobs that decide behaviour, with every value that could be a
    secret dropped. Results are meaningless without the config that made them."""
    keep = ("ENTRY_START_SEC", "ENTRY_CUTOFF_SEC", "FLATTEN_CUTOFF_SEC",
            "MIN_COST", "MAX_COST", "MIN_EDGE", "MAX_SPREAD", "MAX_CONTRACTS",
            "UNCERTAINTY_K", "SLIPPAGE_TICKS", "POLL_SEC", "MAX_TRADES_PER_DAY",
            "PER_TRADE_RISK_USD", "MAX_EXPOSURE_USD", "MAX_DRAWDOWN_PCT",
            "DEFAULT_VOL_BPS", "LOTTO_MAX_ASK")
    out = {}
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() in keep:
                out[k.strip()] = v.split("#")[0].strip()
    return out


def manifest(items, snap, git, cfg):
    now = datetime.datetime.now(TZ)
    L = []
    A = L.append
    A(f"# crypto-15m-bot research backup — {now:%Y-%m-%d %H:%M %Z}\n")
    A("Kalshi 15-minute crypto binary options. Live, real money, micro book only.\n")
    A(f"Code commit: `{git.get('commit','?')[:12]}` — {git.get('subject','')}\n")
    bal = (snap.get("balance") or {}).get("balance_dollars")
    if bal:
        A(f"Account balance at backup: **${bal}**\n")
    A("## What is in here, and how to trust it\n")
    A("| File | What it is | Trustworthy? |")
    A("|---|---|---|")
    A("| `research/observations.jsonl` | Causal log. Fixed checkpoints per live "
      "window: true orderbook both legs, depth, spot, distance to strike, then "
      "the settlement result joined after. | **Yes.** Purpose-built. Only "
      "knowable-at-the-time fields. |")
    A("| `research/history.jsonl` | Retroactive. Settled markets from Kalshi "
      "joined to per-minute candlesticks (yes_ask/yes_bid per minute). One row "
      "per market-minute. | **Yes**, but candle close prices, not the live book "
      "we would have crossed. |")
    A("| `research/drift.jsonl` | Paired samples of the /markets summary quote "
      "vs the real orderbook top. | **Yes.** Measurement only. |")
    A("| `kalshi_settlements.json` | Every settled position, from the exchange. "
      "Authoritative P/L. | **Yes** — but a position that was CUT shows full "
      "cost with zero revenue, so it reads as a total loss when it was not. "
      "Only reconciles cleanly for held-to-settlement trades. |")
    A("| `kalshi_fills.json` | Every execution: price, count, fee, taker flag. | "
      "**Yes.** Exchange record. |")
    A("| `data/*/fills.jsonl` | Our own order attempts INCLUDING rejects, with "
      "book age and round-trip latency. | **Yes**, and the rejects are the "
      "valuable part — they are how the pricing bug was found. |")
    A("| `data/*/lessons_live.jsonl` | One row per closed live position with a "
      "plain-language reason. | Mostly. Slightly under-counts vs the exchange; "
      "prefer `kalshi_settlements.json` for P/L. |")
    A("| `data/*/ladder.jsonl` | Legacy passive observer. | **NO — do not use.** "
      "`min_ask` is the minimum across the WHOLE window written at settlement, "
      "so a loser decays to 0.001 and a winner never gets cheap. Produces a "
      "fake +74% edge. Kept only as a worked example of look-ahead bias. |")
    A("| `data/*/lessons.jsonl` | Paper-trading era. | **NO — do not use.** "
      "Filled at summary asks that cannot be crossed, at sizes the book would "
      "not give, cut costs modelled -5c against a measured -0.4c to -2c. |")
    A("| `config.json` | The behaviour knobs at backup time. Secrets excluded. | "
      "Yes. Results mean nothing without it. |")
    A("| `git.json` | Commit and recent history. | Yes. Ties results to code. |")
    A("\n## Established findings\n")
    A("- The `/markets` summary quotes drift from the real book. The NO leg "
      "understated the true ask in 54% of 56 samples; one BTC window read "
      "0.63/0.38 against a real 0.72/0.29. Orders built off the summary were "
      "accepted and filled nothing — 32 of 35. Pricing off the orderbook took "
      "fill rate from 6.4% to 100%.")
    A("- Buying the cheap leg pays inside roughly 0.02–0.50. Below 0.02 is a "
      "graveyard (0-for-139 on the YES leg). Above 0.50 the sign flips.")
    A("- Edge is concentrated at the FRONT of the window: +11.1% over implied "
      "at 750s remaining (n=258 independent windows, p=0.0001, survives "
      "Bonferroni across 17 buckets). NOT deployed — zero live fills exist "
      "that early. See `research/OPEN_QUESTIONS.md`.")
    A("\n## Statistical trap worth remembering\n")
    A("Ten checkpoints from one window are the same bet observed ten times. "
      "Pooling them inflated a z-score by ~2.8x and turned a p=0.07 into a "
      "p=0.00002. Always collapse to one row per window before testing.\n")
    A("## Files\n")
    for rel, p in items:
        A(f"- `{rel}` — {p.stat().st_size:,} bytes")
    A(f"\n## Config at backup\n\n```json\n{json.dumps(cfg, indent=2)}\n```")
    return "\n".join(L)


def build_zip():
    items = collect()
    snap = api_snapshot()
    git = git_state()
    cfg = safe_config()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, p in items:
            z.write(p, rel)
        z.writestr("MANIFEST.md", manifest(items, snap, git, cfg))
        z.writestr("kalshi_settlements.json",
                   json.dumps(snap.get("settlements"), indent=1))
        z.writestr("kalshi_fills.json", json.dumps(snap.get("fills"), indent=1))
        z.writestr("kalshi_balance.json", json.dumps(snap.get("balance"), indent=1))
        z.writestr("kalshi_positions.json",
                   json.dumps(snap.get("positions"), indent=1))
        z.writestr("config.json", json.dumps(cfg, indent=2))
        z.writestr("git.json", json.dumps(git, indent=2))
    blob = buf.getvalue()
    for marker in SECRET_MARKERS:
        if marker in blob:
            raise SystemExit(f"ABORT: archive contains {marker!r}")
    return blob, items


def upload(blob: bytes, name: str) -> str:
    """rclone, not the Drive API. A service account has no My Drive quota and
    409s/403s on upload; the `chester:` rclone remote is OAuth as the user and
    is what the hyrox backup already uses."""
    import shutil, tempfile
    if not shutil.which("rclone"):
        raise SystemExit("rclone not installed")
    tmp = Path(tempfile.mkdtemp()) / name
    tmp.write_bytes(blob)
    dest = f"chester:crypto15m/{name}"
    subprocess.check_call(["rclone", "copyto", str(tmp), dest, "--quiet"],
                          timeout=600)
    return dest


def prune():
    """Keep the last KEEP dated archives."""
    try:
        out = subprocess.run(["rclone", "lsf", "chester:crypto15m/"],
                             capture_output=True, text=True, timeout=120).stdout
    except Exception:
        return
    zips = sorted(x for x in out.split() if x.startswith(PREFIX) and x.endswith(".zip"))
    for old in zips[:-KEEP]:
        subprocess.run(["rclone", "delete", f"chester:crypto15m/{old}"], timeout=120)
        print(f"  pruned {old}")


def main():
    blob, items = build_zip()
    now = datetime.datetime.now(TZ)
    name = f"{PREFIX}{now:%Y-%m-%d}.zip"
    dest = upload(blob, name)
    prune()
    STATE.write_text(json.dumps({"last": {
        "name": name, "dest": dest, "bytes": len(blob),
        "files": len(items), "at": now.isoformat(timespec="seconds")}}, indent=2))
    print(f"uploaded {dest}  {len(blob):,} bytes  {len(items)} data files + 7 generated")


if __name__ == "__main__":
    main()
