"""Board Memo 004 phase 1 — storm watchdog. Read-only. Pages the chairman.

Does not write LIVE_KILL. Phase 2 (auto-kill after 15 min of (a)+(b)) is a
separate flip, >=3 days after reviewing this alert log. Musk voted against
arming it; do not 'just leave the function in'.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BOOKS = DATA / "books"
HEARTBEAT = BOOKS / "heartbeat"
STATE_PATH = DATA / "watchdog_state.json"
DASH_PATH = DATA / "watchdog.json"
ALERT_LOG = DATA / "watchdog_alerts.jsonl"

LIVE_ASSETS = ("btc", "eth", "sol", "xrp", "doge", "hype", "bnb")
STREAM_UNIT = "crypto15m-stream.service"
ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")

WINDOW_A_SEC = 15 * 60
BOOK_STALE_SEC = 3 * 60
NO_FILL_SEC = 4 * 60 * 60
SETTLE_OVERDUE_SEC = 15 * 60
COOLDOWN_SEC = 30 * 60
STALE_FRAC = 0.20
PHASE2_AUTOKILL = False  # do not arm. Memo 004 phase 1 is alerts only.

MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
          "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


def _now() -> float:
    return time.time()


def ticker_close_utc(ticker: str) -> datetime | None:
    """KXBTC15M-26AUG281300-00 -> 13:00 America/New_York that day, as UTC."""
    try:
        body = ticker.split("-", 1)[1]          # 26AUG281300-00
        stamp, _sfx = body.rsplit("-", 1)
        yy, mon, dd, hhmm = stamp[:2], stamp[2:5], stamp[5:7], stamp[7:11]
        close_et = datetime(
            2000 + int(yy), MONTHS[mon], int(dd),
            int(hhmm[:2]), int(hhmm[2:]), tzinfo=ET,
        )
        return close_et.astimezone(timezone.utc)
    except (IndexError, KeyError, ValueError):
        return None


def current_window_tickers(now: datetime | None = None) -> dict[str, str]:
    from crypto15m.kalshi.stream import synthesize_window_tickers
    return synthesize_window_tickers(now)


def stream_active(checker=None) -> bool:
    if checker is not None:
        return bool(checker())
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", STREAM_UNIT],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() == "active"
    except (OSError, subprocess.TimeoutExpired):
        return False


def _parse_log_ts(line: str, default_tz=CT) -> float | None:
    # 2026-08-28 11:44:14,024 INFO ...
    if len(line) < 19 or line[4] != "-":
        return None
    try:
        naive = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
        return naive.replace(tzinfo=default_tz).timestamp()
    except ValueError:
        return None


def scan_log(path: Path, now: float, window_sec: float = WINDOW_A_SEC) -> tuple[int, int]:
    """(ticks, bad_ticks) in the last window_sec of a live.log.

    A tick is a tape line (starts with KX) or a LIVE step failed line.
    Bad: 429, book_stale, orderbook read failed.

    Tape lines from print() have no timestamp. Walk from the end of the file
    and treat untimestamped lines as `now` until we hit a stamp older than
    the window — the tail of a live.log is the present.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return 0, 0
    if len(data) > 1_000_000:
        data = data[-1_000_000:]
    lines = data.decode("utf-8", errors="replace").splitlines()
    cutoff = now - window_sec
    ticks = 0
    bad = 0
    assumed = now
    for line in reversed(lines):
        ts = _parse_log_ts(line)
        if ts is not None:
            assumed = ts
        if assumed < cutoff:
            break
        is_tick = line.startswith("KX") or line.startswith("LIVE step failed")
        is_warn = ("orderbook read failed" in line or "declined: book_stale" in line)
        if not is_tick and not is_warn:
            continue
        if is_tick:
            ticks += 1
        if is_warn or "429" in line or "book_stale" in line:
            bad += 1
            if not is_tick:
                ticks += 1
    return ticks, bad


def last_fill_ts(data_dir: Path = DATA) -> float | None:
    latest = None
    for a in LIVE_ASSETS:
        p = data_dir / a / "fills.jsonl"
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if int(d.get("filled") or 0) <= 0:
                continue
            ts = d.get("ts")
            if not ts:
                continue
            try:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            latest = t if latest is None else max(latest, t)
            break
    return latest


def unsettled_overdue(data_dir: Path = DATA, now: datetime | None = None) -> list[dict]:
    """Trigger (e): live fill whose window closed >15 min ago with no settle."""
    now = now or datetime.now(timezone.utc)
    out = []
    for a in LIVE_ASSETS:
        fills_path = data_dir / a / "fills.jsonl"
        journal = data_dir / a / "journal.sqlite3"
        try:
            fill_lines = fills_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        settled: set[str] = set()
        if journal.exists():
            try:
                import sqlite3
                con = sqlite3.connect(f"file:{journal}?mode=ro", uri=True, timeout=2)
                settled = {r[0] for r in con.execute(
                    "SELECT DISTINCT ticker FROM events WHERE kind='settle'")}
                con.close()
            except Exception:
                settled = set()
        seen = set()
        for line in reversed(fill_lines):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if int(d.get("filled") or 0) <= 0:
                continue
            ticker = d.get("ticker")
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            if ticker in settled:
                continue
            close = ticker_close_utc(ticker)
            if close is None:
                continue
            overdue = (now - close).total_seconds()
            # Ignore fossils from the paper/split era (8/24 fills still in
            # fills.jsonl). A stuck position the chairman can still act on
            # closed in the last 12 hours.
            if SETTLE_OVERDUE_SEC < overdue <= 12 * 3600:
                out.append({"asset": a, "ticker": ticker,
                            "overdue_sec": int(overdue)})
    return out


def collect_alerts(now: float | None = None, *,
                   data_dir: Path = DATA,
                   stream_is_active: bool | None = None) -> list[dict]:
    now = now if now is not None else _now()
    now_dt = datetime.fromtimestamp(now, timezone.utc)
    alerts: list[dict] = []

    active = stream_is_active if stream_is_active is not None else stream_active()

    # (d) stream unit inactive
    if not active:
        alerts.append({"id": "d:stream", "level": "crit",
                       "msg": "stream unit inactive"})

    # (a) >20% of last-15-min ticks 429/stale
    for a in LIVE_ASSETS:
        ticks, bad = scan_log(data_dir / a / "live.log", now)
        if ticks >= 20 and bad / ticks > STALE_FRAC:
            alerts.append({
                "id": f"a:{a}", "level": "warn",
                "msg": f"{a}: {bad}/{ticks} ticks 429/stale "
                       f"({bad/ticks:.0%}) last 15m",
                "asset": a, "ticks": ticks, "bad": bad,
            })

    # (b) no current-window book >3 min while stream runs
    if active:
        try:
            tickers = current_window_tickers(now_dt)
        except Exception:
            tickers = {}
        from crypto15m.assets import ASSETS
        for a in LIVE_ASSETS:
            series = ASSETS[a].series
            ticker = tickers.get(series)
            if not ticker:
                continue
            p = (data_dir / "books" / f"{ticker}.json") if data_dir != DATA else (BOOKS / f"{ticker}.json")
            # tests pass a temp data_dir; books live at data_dir/books
            p = data_dir / "books" / f"{ticker}.json"
            try:
                age = now - p.stat().st_mtime
            except OSError:
                close = ticker_close_utc(ticker)
                opened = (close.timestamp() - 900) if close else now
                age = now - opened
            if age > BOOK_STALE_SEC:
                alerts.append({
                    "id": f"b:{a}", "level": "warn",
                    "msg": f"{a}: no current book {ticker} ({age:.0f}s old)",
                    "asset": a, "ticker": ticker,
                })

    # (c) zero fills for 4 open-market hours, but only if traders are alive
    alive = False
    for a in LIVE_ASSETS:
        try:
            if now - (data_dir / a / "live.log").stat().st_mtime < 120:
                alive = True
                break
        except OSError:
            pass
    last = last_fill_ts(data_dir)
    if alive and last is not None and (now - last) > NO_FILL_SEC:
        hrs = (now - last) / 3600
        alerts.append({
            "id": "c:nofill", "level": "warn",
            "msg": f"zero fills for {hrs:.1f}h (traders alive)",
        })

    # (e) closed but unsettled >15 min
    for u in unsettled_overdue(data_dir, now_dt):
        alerts.append({
            "id": f"e:{u['ticker']}", "level": "warn",
            "msg": f"{u['asset']}: {u['ticker']} closed, unsettled "
                   f"{u['overdue_sec']//60}m",
            **u,
        })

    return alerts


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {"sent": {}}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE_PATH)


def ntfy_topic() -> str:
    # Prefer env; fall back to .env without importing dotenv (watchdog is a script).
    t = os.environ.get("NTFY_TOPIC", "").strip()
    if t:
        return t
    env = ROOT / ".env"
    try:
        for line in env.read_text().splitlines():
            if line.startswith("NTFY_TOPIC="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def send_ntfy(topic: str, title: str, body: str, priority: str = "high") -> bool:
    if not topic:
        return False
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": priority,
            "Tags": "warning,crypto15m",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def write_dash(alerts: list[dict], now: float) -> None:
    payload = {
        "ts": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "ok": not alerts,
        "n": len(alerts),
        "alerts": alerts,
        "phase2_autokill": PHASE2_AUTOKILL,
    }
    DASH_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DASH_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(DASH_PATH)


def log_alerts(alerts: list[dict], now: float) -> None:
    if not alerts:
        return
    ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": now, "alerts": alerts}
    with ALERT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def run() -> int:
    assert PHASE2_AUTOKILL is False, "phase 2 is not authorized"
    now = _now()
    alerts = collect_alerts(now)
    write_dash(alerts, now)
    log_alerts(alerts, now)
    state = _load_state()
    sent = state.setdefault("sent", {})
    topic = ntfy_topic()
    new = []
    for a in alerts:
        last = float(sent.get(a["id"], 0))
        if now - last < COOLDOWN_SEC:
            continue
        new.append(a)
        sent[a["id"]] = now
    if new:
        title = f"crypto15m watchdog ({len(new)})"
        body = "\n".join(a["msg"] for a in new)
        send_ntfy(topic, title, body,
                  priority="urgent" if any(a["level"] == "crit" for a in new) else "high")
        _save_state(state)
    # Drop stale sent keys older than 24h so the file cannot grow forever.
    cutoff = now - 86400
    state["sent"] = {k: v for k, v in sent.items() if v >= cutoff}
    _save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
