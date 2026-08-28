"""Board Memo 007 — live-only replay of microstructure candidates.

No orders. Writes research/memo007_report.md and data/memo007_report.json.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from crypto15m.study.micro_alts import reconstruct_from_fill  # noqa: E402

DATA = ROOT / "data"
OUT_JSON = DATA / "memo007_report.json"
OUT_MD = ROOT / "research" / "memo007_report.md"
LIVE_ASSETS = ("btc", "eth", "sol", "xrp", "doge", "hype", "bnb")
DEGRADED = (
    datetime(2026, 8, 28, 1, 5, tzinfo=timezone.utc),
    datetime(2026, 8, 28, 4, 45, tzinfo=timezone.utc),
)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (float("nan"), float("nan"))
    p = wins / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def load_settles() -> dict[str, dict]:
    """ticker -> last micro settle {result, pnl, ts, won, side}."""
    out: dict[str, dict] = {}
    for a in LIVE_ASSETS:
        db = DATA / a / "journal.sqlite3"
        if not db.exists():
            continue
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        for ts, ticker, payload in con.execute(
            "SELECT ts, ticker, payload FROM events WHERE kind='settle' ORDER BY id"
        ):
            d = json.loads(payload)
            if d.get("book") != "micro":
                continue
            out[ticker] = {
                "asset": a, "ts": ts, "result": d.get("result"),
                "pnl": float(d.get("pnl") or 0), "won": bool(d.get("won")),
                "side": d.get("side"),
            }
        con.close()
    return out


def load_fills() -> list[dict]:
    rows = []
    for a in LIVE_ASSETS:
        p = DATA / a / "fills.jsonl"
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if int(d.get("filled") or 0) <= 0:
                continue
            if str(d.get("side", "")).startswith("exit_"):
                continue
            d["asset"] = a
            rows.append(d)
    rows.sort(key=lambda r: r.get("ts", ""))
    return rows


def implied_from_fill(d: dict) -> float | None:
    try:
        q = float(d["quoted_px"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (0 < q < 1):
        return None
    side = d.get("side")
    if side == "yes":
        return q
    if side == "no":
        return 1.0 - q
    return None


def main() -> int:
    settles = load_settles()
    fills = load_fills()
    joined = []
    for f in fills:
        s = settles.get(f["ticker"])
        if not s:
            continue
        try:
            ts = _parse(f["ts"])
        except (KeyError, ValueError):
            continue
        degraded = DEGRADED[0] <= ts <= DEGRADED[1]
        day = ts.astimezone(timezone.utc).date().isoformat()
        try:
            p = float(f["p_yes"])
        except (KeyError, TypeError, ValueError):
            continue
        y = 1.0 if s["result"] == "yes" else 0.0
        implied = implied_from_fill(f)
        remaining = float(f.get("remaining_sec") or 0)
        alts = reconstruct_from_fill(p, implied, remaining) if implied else {"d_control": p}
        joined.append({
            "asset": f["asset"], "ticker": f["ticker"], "ts": f["ts"],
            "day": day, "side": f.get("side"), "result": s["result"],
            "won": s["side"] == s["result"] if s.get("side") else s["won"],
            "pnl": s["pnl"], "p_yes": p, "y": y,
            "brier": (p - y) ** 2, "degraded": degraded,
            "remaining_sec": remaining, "alts": alts,
        })

    def bucket(rows, key=lambda r: r["day"]):
        g = defaultdict(list)
        for r in rows:
            g[key(r)].append(r)
        out = {}
        for k, rs in sorted(g.items()):
            n = len(rs)
            w = sum(1 for r in rs if r["won"])
            lo, hi = _wilson(w, n)
            out[k] = {
                "n": n, "wins": w, "wr": w / n if n else None,
                "wr_ci95": [lo, hi],
                "pnl": round(sum(r["pnl"] for r in rs), 4),
                "brier": round(sum(r["brier"] for r in rs) / n, 5) if n else None,
            }
        return out

    clean = [r for r in joined if not r["degraded"]]
    degraded = [r for r in joined if r["degraded"]]
    by_day = bucket(joined)
    by_day_clean = bucket(clean)

    # remaining-time buckets on clean sample
    def rem_key(r):
        rem = r["remaining_sec"]
        if rem >= 300:
            return ">=300s"
        if rem >= 150:
            return "150-300s"
        return "<150s"
    by_rem = bucket(clean, rem_key)

    # candidate Brier on clean fills that have implied
    scored = [r for r in clean if "a_time" in r["alts"]]
    cand = {}
    for name in ("d_control", "a_time", "a_regime", "b_unc", "c_both"):
        if not scored:
            cand[name] = {"n": 0}
            continue
        briers = []
        # skip-if-weak: if alt p closer to 0.5 than 0.08, don't take the trade
        pnl_taken = 0.0
        n_taken = 0
        for r in scored:
            p = r["alts"][name]
            briers.append((p - r["y"]) ** 2)
            if abs(p - 0.5) >= 0.08:
                pnl_taken += r["pnl"]
                n_taken += 1
        cand[name] = {
            "n": len(scored),
            "brier": round(sum(briers) / len(briers), 5),
            "taken": n_taken,
            "pnl_if_skip_weak": round(pnl_taken, 4),
        }

    # WR drop 8/26-27 pooled vs 8/28 clean
    def pool(days):
        rs = [r for r in clean if r["day"] in days]
        n = len(rs)
        w = sum(1 for r in rs if r["won"])
        return n, w, _wilson(w, n), round(sum(r["pnl"] for r in rs), 4)
    n_pre, w_pre, ci_pre, pnl_pre = pool(["2026-08-26", "2026-08-27"])
    n_28, w_28, ci_28, pnl_28 = pool(["2026-08-28"])

    # Winner: lowest Brier on clean sample among candidates that change p.
    # If none beat control by a meaningful margin, control wins.
    winner = "d_control"
    if scored:
        best = min(cand.items(), key=lambda kv: kv[1].get("brier", 9) if kv[1].get("n") else 9)
        if best[1].get("brier") is not None and cand["d_control"].get("brier") is not None:
            if cand["d_control"]["brier"] - best[1]["brier"] >= 0.002:
                winner = best[0]

    report = {
        "asof": datetime.now(timezone.utc).isoformat(),
        "n_joined": len(joined),
        "n_clean": len(clean),
        "n_degraded": len(degraded),
        "by_day": by_day,
        "by_day_clean": by_day_clean,
        "by_remaining_clean": by_rem,
        "degraded_window": {
            "n": len(degraded),
            "wins": sum(1 for r in degraded if r["won"]),
            "pnl": round(sum(r["pnl"] for r in degraded), 4),
        },
        "pre_828": {"n": n_pre, "wins": w_pre,
                    "wr": w_pre / n_pre if n_pre else None, "wr_ci95": list(ci_pre),
                    "pnl": pnl_pre},
        "day_828_clean": {"n": n_28, "wins": w_28,
                          "wr": w_28 / n_28 if n_28 else None, "wr_ci95": list(ci_28),
                          "pnl": pnl_28},
        "candidates": cand,
        "winner": winner,
        "note": (
            "Live-only fills.jsonl joined to micro settles. Degraded window "
            "01:05-04:45Z 8/28 excluded from candidate fit. No live expert "
            "was edited. Winner is lowest Brier vs control; margin 0.002 or "
            "control stays. Shadow logger writes all alt p_yes going forward."
        ),
    }
    # fix accidental typo key if any - rewrite pre_828 cleanly
    report["pre_828"] = {"n": n_pre, "wins": w_pre,
                         "wr": (w_pre / n_pre) if n_pre else None,
                         "wr_ci95": list(ci_pre), "pnl": pnl_pre}

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2, default=str))

    def pct(x):
        return "n/a" if x is None else f"{100*x:.1f}%"

    def ci(pair):
        if not pair or pair[0] != pair[0]:
            return ""
        return f"  95% CI [{100*pair[0]:.1f}%, {100*pair[1]:.1f}%]"

    lines = [
        "# Memo 007 replay — live-only microstructure study",
        f"*Run {report['asof']}. No live expert was changed.*",
        "",
        "## Sample",
        f"- Joined live fills to micro settles: **{len(joined)}**",
        f"- Clean (outside 01:05–04:45Z 8/28 Gaussian window): **{len(clean)}**",
        f"- Flagged degraded window: **{len(degraded)}** trades, "
        f"{report['degraded_window']['wins']} wins, "
        f"P/L ${report['degraded_window']['pnl']:+.2f}",
        "",
        "## Win rate by day (live-only, all joined)",
    ]
    for day, s in by_day.items():
        lines.append(f"- {day}: {s['wins']}/{s['n']} = {pct(s['wr'])}"
                     f"{ci(s['wr_ci95'])}  P/L ${s['pnl']:+.2f}  Brier {s['brier']}")
    lines += [
        "",
        "## Is the 8/28 drop distinguishable from noise?",
        f"- 8/26–27 pooled (clean): {w_pre}/{n_pre} = {pct(w_pre/n_pre if n_pre else None)}"
        f"{ci(ci_pre)}  P/L ${pnl_pre:+.2f}",
        f"- 8/28 clean: {w_28}/{n_28} = {pct(w_28/n_28 if n_28 else None)}"
        f"{ci(ci_28)}  P/L ${pnl_28:+.2f}",
    ]
    overlap = (n_pre and n_28 and ci_pre[0] <= ci_28[1] and ci_28[0] <= ci_pre[1])
    lines.append(
        "- Confidence intervals **overlap** — the drop is suggestive, not significant."
        if overlap else
        "- Confidence intervals **do not overlap** — treat as a real regime shift until proven otherwise."
    )
    lines += ["", "## Remaining-time buckets (clean)",]
    for k, s in by_rem.items():
        lines.append(f"- {k}: {s['wins']}/{s['n']} = {pct(s['wr'])}  P/L ${s['pnl']:+.2f}  Brier {s['brier']}")
    lines += ["", "## Candidates (Brier on clean fills with a quoted implied)",
              "Lower Brier is better. `pnl_if_skip_weak` skips the trade when |p-0.5|<0.08.",]
    for name, s in cand.items():
        if not s.get("n"):
            lines.append(f"- `{name}`: no sample")
            continue
        mark = " ← winner" if name == winner else ""
        lines.append(
            f"- `{name}`: Brier {s['brier']}  taken {s['taken']}/{s['n']}  "
            f"P/L if skip-weak ${s['pnl_if_skip_weak']:+.2f}{mark}"
        )
    lines += [
        "",
        f"**Winner for 24h shadow: `{winner}`.** Live entries still use control.",
        "Shadow logger (bot.py prediction payload `shadow_007`) records every "
        "candidate alongside live micro p_yes. Deploying a winner as the live "
        "expert is a separate chairman decision. Memo 002's 200-trade gate still applies.",
        "",
        "## Standing corrections (from the memo)",
        "- Live book reads microstructure directly; `gate()` is display-only.",
        "- 8/28 was +$0.35 live, not −$7.19 (that mixed shadow rows).",
        "- Degraded-window fills were +$2.47 on 6 trades; they are flagged, not fit.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n")
    print(OUT_MD.read_text())
    print("wrote", OUT_JSON, OUT_MD, "winner", winner)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
