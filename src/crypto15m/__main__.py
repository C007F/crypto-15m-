from __future__ import annotations

import argparse
import sys
import time
import zlib

from crypto15m.bot import Mind, render_watch
from crypto15m.config import load_settings
from crypto15m.kalshi.client import KalshiClient
from crypto15m.meta.gate import gate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crypto15m", description="Kalshi KXBTC15M mind. Paper by default.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_watch = sub.add_parser("watch", help="Print live tape. No orders.")
    p_watch.add_argument("--once", action="store_true")
    sub.add_parser("probe", help="One snapshot + expert mix. No orders.")
    sub.add_parser("fit", help="Bootstrap logistic on Coinbase 15m history.")
    sub.add_parser("paper", help="Paper loop — only when Cam says so.")
    p_live = sub.add_parser("live", help="Real orders. Dangerous.")
    p_live.add_argument("--i-understand-risk", action="store_true")
    sub.add_parser("status", help="Recent journal events.")
    sub.add_parser("whoami", help="Kalshi balance (needs API key).")
    args = parser.parse_args(argv)
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

    if args.cmd == "paper":
        # Deterministic per-asset offset keeps five traders from hitting Kalshi
        # in the same instant; the exchange 429s the whole box, not one process.
        offset = (zlib.crc32(settings.asset.key.encode()) % 1000) / 1000.0
        time.sleep(offset * settings.poll_sec)
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
            time.sleep(settings.poll_sec + backoff)

    if args.cmd == "live":
        if not args.i_understand_risk:
            print("Refusing live without --i-understand-risk", file=sys.stderr)
            return 2
        live = Mind(settings, live=True)
        while True:
            print(live.step())
            time.sleep(settings.poll_sec)

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
