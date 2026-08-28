from __future__ import annotations

import argparse
import logging
import sys
import time
import zlib

from crypto15m.assets import poll_jitter_sec
from crypto15m.bot import Mind, render_watch
from crypto15m.config import load_settings
from crypto15m.kalshi.client import KalshiClient
from crypto15m.meta.gate import gate


def _configure_logging() -> None:
    """Board Memo 006 part 3b.

    No logging config existed in the live path, so Python's lastResort handler
    emitted WARNING and above only: every 'empirical moves from N candles'
    success line was structurally invisible. Fourteen thousand failures were in
    the logs and not one success, which made "did the rebuild ever work" an
    unanswerable question during the 8/27-28 storm.

    Root stays at WARNING and urllib3 is pinned there explicitly - turning on
    INFO globally would put request-level chatter into a live.log that is
    already 24MB per asset.
    """
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("crypto15m").setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crypto15m", description="Kalshi KXBTC15M mind. Paper by default.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_watch = sub.add_parser("watch", help="Print live tape. No orders.")
    p_watch.add_argument("--once", action="store_true")
    sub.add_parser("livecheck", help="place ONE real 1-contract order and verify the path")
    sub.add_parser("probe", help="One snapshot + expert mix. No orders.")
    sub.add_parser("fit", help="Bootstrap logistic on Coinbase 15m history.")
    sub.add_parser("paper", help="Paper loop — only when Cam says so.")
    p_live = sub.add_parser("live", help="Real orders. Dangerous.")
    p_live.add_argument("--i-understand-risk", action="store_true")
    sub.add_parser("status", help="Recent journal events.")
    sub.add_parser("whoami", help="Kalshi balance (needs API key).")
    args = parser.parse_args(argv)
    _configure_logging()
    settings = load_settings()
    mind = Mind(settings, live=False)

    if args.cmd == "fit":
        from crypto15m.models.bootstrap import train_and_save

        path = settings.data_dir / "models" / "logistic.json"
        print("fitting bootstrap logistic…")
        result = train_and_save(path, hours=24)
        print(result)
        return 0 if result.get("ok") else 1

    if args.cmd in {"probe", "watch"}:
        once = args.cmd == "probe" or getattr(args, "once", False)
        if args.cmd == "watch":
            print("watch KXBTC15M (no orders). Ctrl+C to stop.")
        while True:
            frame = mind.features()
            if frame is None:
                print("no open market")
            else:
                views = mind._views(frame)
                view = gate(views)
                print(render_watch(frame, view, views))
                if args.cmd == "probe":
                    # Equity/risk live on each TrialBook since the multi-book
                    # refactor; Mind itself no longer holds a portfolio.
                    for book in mind.books:
                        snap = book.portfolio.snapshot(frame.asof)
                        cap = book.governor.effective_daily_cap(snap.day_start_equity)
                        print(f"  {book.spec.book_id:9} equity={snap.equity} "
                              f"daily_cap={cap} halt={book.governor.halt.value}")
            if once:
                return 0
            time.sleep(settings.poll_sec)

    if args.cmd == "livecheck":
        from crypto15m.livecheck import run as _livecheck
        return _livecheck(settings)

    if args.cmd == "paper":
        # Deterministic per-asset offset keeps five traders from hitting Kalshi
        # in the same instant; the exchange 429s the whole box, not one process.
        # Spread startup across several poll intervals. All coins re-list
        # their window ticker at once on boot and at each 15m rotation; that
        # simultaneous burst is what draws 429s, not the steady-state rate.
        offset = (zlib.crc32(settings.asset.key.encode()) % 1000) / 1000.0
        time.sleep(offset * settings.poll_sec * 3)
        print("PAPER loop. look often, trade rarely. daily_cap", settings.daily_loss_usd)
        backoff = 0.0
        while True:
            try:
                print(mind.step())
                backoff = 0.0
            except Exception as exc:
                # A transient 429 or network blip used to kill the process and
                # leave systemd restarting it in a tight loop, which made the
                # rate limit worse. Back off instead, up to a minute.
                backoff = min(60.0, max(2.0, backoff * 2))
                print(f"step failed ({type(exc).__name__}: {exc}); "
                      f"backing off {backoff:.0f}s", flush=True)
            time.sleep(settings.poll_sec + backoff + poll_jitter_sec(settings.asset.key))

    if args.cmd == "live":
        if not args.i_understand_risk:
            print("Refusing live without --i-understand-risk", file=sys.stderr)
            return 2
        live = Mind(settings, live=True)
        # Same backoff as paper. A 429 must not kill the process and leave
        # systemd restarting it into the same rate limit while a real position
        # sits open and unmanaged.
        offset = (zlib.crc32(settings.asset.key.encode()) % 1000) / 1000.0
        time.sleep(offset * settings.poll_sec * 3)
        backoff = 0.0
        while True:
            try:
                print(live.step())
                backoff = 0.0
            except Exception as exc:
                backoff = min(60.0, max(2.0, backoff * 2))
                print(f"LIVE step failed ({type(exc).__name__}: {exc}); "
                      f"backing off {backoff:.0f}s", flush=True)
            time.sleep(settings.poll_sec + backoff + poll_jitter_sec(settings.asset.key))

    if args.cmd == "status":
        for row in mind.journal.recent():
            print(row["id"], row["ts"], row["kind"], row["ticker"], row["payload"][:180])
        return 0

    if args.cmd == "whoami":
        print(KalshiClient(settings).get_balance())
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
