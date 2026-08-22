#!/usr/bin/env python3
"""Create data/<asset>/ for every configured coin.

systemd's `StandardOutput=append:` applies to ExecStartPre as well, so a unit
cannot create the directory its own log lives in - a brand new coin dies with
status 209/STDOUT before Python runs. This oneshot runs first and logs to the
journal, so adding a coin to assets.py is genuinely the only step required.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from crypto15m.assets import ASSETS  # noqa: E402

made = []
for key in ASSETS:
    d = ROOT / "data" / key
    if not d.exists():
        d.mkdir(parents=True, exist_ok=True)
        made.append(key)
print(f"data dirs ready for {len(ASSETS)} assets" + (f"; created {made}" if made else ""))
