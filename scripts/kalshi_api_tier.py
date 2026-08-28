#!/usr/bin/env python3
"""Inspect (and optionally request) the Kalshi API usage tier.

Deliberately standalone. KalshiClient carries an endpoint allowlist - "the bot
can read markets, read its balance, and place orders; it cannot move money" -
and /account/* is not on it. Widening that rail for a one-off admin query would
be a bad trade, so this script signs its own requests with the same key and
leaves the trading client untouched.

    python scripts/kalshi_api_tier.py              # read current tier
    python scripts/kalshi_api_tier.py --upgrade    # request the Advanced grant

The upgrade is described by Kalshi as a PERMANENT grant, so it is behind an
explicit flag and a typed confirmation.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from crypto15m.config import load_settings

# Kalshi has renamed these between doc revisions; try each and report whichever
# answers rather than guessing one and reporting a 404 as "no access".
READ_PATHS = (
    "/account/limits",           # the real one: usage_tier, buckets, grants
    "/account/endpoint_costs",   # token cost per endpoint
)
UPGRADE_PATH = "/account/api_usage_level/upgrade"


def _sign(settings, method: str, path: str) -> dict[str, str]:
    url = f"{settings.rest_base}{path}"
    ts = str(int(time.time() * 1000))
    msg = f"{ts}{method.upper()}{urlparse(url).path}".encode()
    with settings.private_key_path.open("rb") as fh:
        key = serialization.load_pem_private_key(fh.read(), password=None)
    sig = key.sign(
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": settings.api_key_id,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
    }


def _call(settings, method: str, path: str, body: dict | None = None):
    url = f"{settings.rest_base}{path}"
    headers = _sign(settings, method, path)
    if method == "POST":
        headers["Content-Type"] = "application/json"
        r = requests.post(url, headers=headers, data=json.dumps(body or {}), timeout=20)
    else:
        r = requests.get(url, headers=headers, timeout=20)
    try:
        payload = r.json()
    except ValueError:
        payload = {"raw": r.text[:400]}
    return r.status_code, payload


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upgrade", action="store_true",
                    help="request the permanent Advanced grant")
    args = ap.parse_args(argv)

    settings = load_settings()
    print(f"base: {settings.rest_base}")
    print(f"key id: {settings.api_key_id[:8]}…\n")

    found = False
    for path in READ_PATHS:
        code, payload = _call(settings, "GET", path)
        if code == 404:
            print(f"  GET {path:34} 404 (not this name)")
            continue
        found = True
        print(f"\nGET {path}  ->  HTTP {code}")
        print(json.dumps(payload, indent=2)[:2000])
    if not found:
        print("\nNo account tier endpoint answered. Either the paths have moved "
              "again or this key lacks account scope.")

    if args.upgrade:
        print(f"\n{'='*60}\nPERMANENT change: {UPGRADE_PATH}")
        print("Kalshi describes the Advanced grant as permanent and irreversible.")
        if input("Type UPGRADE to proceed: ").strip() != "UPGRADE":
            print("aborted; nothing sent.")
            return 1
        code, payload = _call(settings, "POST", UPGRADE_PATH, {})
        print(f"POST {UPGRADE_PATH} -> HTTP {code}")
        print(json.dumps(payload, indent=2)[:2000])
        return 0 if code in (200, 201) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
