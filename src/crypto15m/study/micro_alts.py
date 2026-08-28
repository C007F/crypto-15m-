"""Board Memo 007 — alternative microstructure scores. Logging / replay only.

Live entries still read MicrostructureExpert.predict() unchanged. These
functions exist so we can score (a)(b)(c) against (d) control without
touching the expert that spends money.
"""
from __future__ import annotations

import math
from decimal import Decimal

from crypto15m.domain import ExpertView
from crypto15m.perception.features import FeatureFrame


def _clip(p: float) -> float:
    return min(max(p, 0.001), 0.999)


def _unc(spread: float, lag: float, remaining: float | None = None,
         dist_bps: float | None = None, sigma: float | None = None) -> float:
    unc = 0.2 + spread * 2.0 + (0.35 if abs(lag) < 0.04 else 0.05)
    if remaining is not None:
        # Complaint in the memo: conviction is identical at 8 min and 80s.
        # More time left → the gap is more likely a trend. Raise unc.
        unc += 0.20 * min(max(remaining, 0.0) / 900.0, 1.0)
    if dist_bps is not None and sigma and sigma > 0:
        z = abs(dist_bps) / sigma
        unc -= 0.12 * min(z / 2.0, 1.0)
    return min(max(unc, 0.08), 0.95)


def _fair_and_implied(frame: FeatureFrame) -> tuple[float, float, float]:
    remaining = max(frame.remaining_sec, 1.0)
    sigma = float(frame.vol_bps_15m) * math.sqrt(remaining / 900.0)
    sigma = max(sigma, 1.0)
    fair = 1.0 / (1.0 + math.exp(-1.702 * (float(frame.distance_bps) / sigma)))
    if frame.hist_p_yes is not None:
        fair = 0.5 * fair + 0.5 * float(frame.hist_p_yes)
    implied = float(frame.implied_p_yes) if frame.implied_p_yes > 0 else 0.5
    return fair, implied, sigma


def variants_from_frame(frame: FeatureFrame) -> dict[str, dict[str, float]]:
    fair, implied, sigma = _fair_and_implied(frame)
    lag = fair - implied
    spread = float(frame.spread)
    remaining = max(frame.remaining_sec, 1.0)
    k = 0.65
    k_time = k * min(remaining / 900.0, 1.0)          # (a) less push as expiry nears
    # (a-regime): shrink the push when |distance| is large vs remaining vol —
    # that's the trending-tape case Musk wants scored.
    z = abs(float(frame.distance_bps)) / sigma
    k_reg = k * (0.35 if z > 1.0 else 1.0)
    out = {
        "d_control": {
            "p": _clip(implied + k * lag),
            "u": _unc(spread, lag),
        },
        "a_time": {
            "p": _clip(implied + k_time * lag),
            "u": _unc(spread, lag),
        },
        "a_regime": {
            "p": _clip(implied + k_reg * lag),
            "u": _unc(spread, lag),
        },
        "b_unc": {
            "p": _clip(implied + k * lag),
            "u": _unc(spread, lag, remaining, float(frame.distance_bps), sigma),
        },
        "c_both": {
            "p": _clip(implied + min(k_time, k_reg) * lag),
            "u": _unc(spread, lag, remaining, float(frame.distance_bps), sigma),
        },
    }
    return out


def shadow_from_frame(frame: FeatureFrame, views: list[ExpertView] | None = None) -> dict:
    """Compact payload for the prediction journal. Must never raise into bot.py."""
    alts = variants_from_frame(frame)
    live = None
    if views:
        for v in views:
            if v.name == "microstructure":
                live = float(v.p_yes)
                break
    return {
        "live_micro": live,
        "alts": {k: {"p": round(v["p"], 4), "u": round(v["u"], 4)} for k, v in alts.items()},
    }


def reconstruct_from_fill(p_yes: float, implied: float, remaining_sec: float) -> dict[str, float]:
    """Identity invert of p = implied + 0.65*(fair-implied), then rescore.

    Used on historical fills where we have p_yes + a quoted cheap-leg, not a
    FeatureFrame. Returns candidate p_yes values only (Brier), not unc.
    """
    implied = min(max(float(implied), 0.001), 0.999)
    p = min(max(float(p_yes), 0.001), 0.999)
    lag = (p - implied) / 0.65
    rem = max(float(remaining_sec), 1.0)
    return {
        "d_control": p,
        "a_time": _clip(implied + 0.65 * min(rem / 900.0, 1.0) * lag),
        "a_regime": p,          # cannot see |distance| from a fill alone
        "b_unc": p,             # unc-only candidate does not change p
        "c_both": _clip(implied + 0.65 * min(rem / 900.0, 1.0) * lag),
    }
