#!/usr/bin/env python3
"""Fill data/history/ before the traders need it. Board Memo 006 part 1.

Persistence only helps if a cache exists at the moment of the restart. Without
this, the FIRST restart after deploying Memo 006 still stampedes Coinbase -
seven processes, ~25 paginated pages each - which is the exact failure the memo
removes. So we fetch once, serially, from a single process, and let the
traders read the files.

Safe to run against the live system: the currently-running traders do not know
data/history/ exists, so this cannot change their behaviour. Read-only against
Coinbase, write-only into a new directory.

    python scripts/prewarm_history.py            # all live assets
    python scripts/prewarm_history.py bnb eth    # named assets
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests

from crypto15m.assets import ASSETS
from crypto15m.bot import NO_LIVE_ASSETS
from crypto15m.perception.history import EmpiricalMoves

log = logging.getLogger("prewarm")


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    logging.getLogger("crypto15m").setLevel(logging.INFO)

    keys = argv or [k for k in ASSETS if k not in NO_LIVE_ASSETS]
    session = requests.Session()
    ok, skipped, failed = [], [], []

    for i, key in enumerate(keys):
        asset = ASSETS.get(key)
        if asset is None:
            log.warning("unknown asset %r; skipping", key)
            failed.append(key)
            continue
        em = EmpiricalMoves(asset.spot_product)
        if em.load_cached():
            age_h = (time.time() - em.built_at) / 3600.0
            log.info("%-9s cache already fresh (%.1fh old); left alone",
                     asset.spot_product, age_h)
            skipped.append(key)
            continue
        # Serial, one product at a time. _fetch_closes already sleeps 0.15s
        # between pages; the herd version of this is what drew the 429s.
        if em.build(session):
            n = len(em.moves.get(1, ()))
            log.info("%-9s built and persisted (%d samples/horizon)",
                     asset.spot_product, n)
            ok.append(key)
        else:
            log.error("%-9s FAILED - traders will rebuild with backoff",
                      asset.spot_product)
            failed.append(key)
        if i + 1 < len(keys):
            time.sleep(2.0)             # breathing room between products

    log.info("")
    log.info("prewarmed %d, already fresh %d, failed %d  ->  %s",
             len(ok), len(skipped), len(failed), EmpiricalMoves.CACHE_DIR)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
