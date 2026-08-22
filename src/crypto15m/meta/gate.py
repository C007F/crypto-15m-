from __future__ import annotations

import math
from decimal import Decimal

from crypto15m.domain import ExpertView


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1.0 - p))


def _sigmoid(z: float) -> float:
    if z < -30:
        return 0.0
    if z > 30:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def gate(views: list[ExpertView]) -> ExpertView:
    """Inverse-uncertainty mix on logit scale. Disagreement raises uncertainty (selectivity)."""
    if not views:
        raise ValueError("no expert views")
    usable = [v for v in views if float(v.uncertainty) < 0.89]
    if not usable:
        usable = views
    weights = []
    logits = []
    ps = []
    for v in usable:
        w = (1.0 - float(v.uncertainty)) ** 2
        weights.append(max(w, 1e-6))
        logits.append(_logit(float(v.p_yes)))
        ps.append(float(v.p_yes))
    wsum = sum(weights)
    mixed = sum(w * l for w, l in zip(weights, logits)) / wsum
    p = min(max(_sigmoid(mixed), 0.01), 0.99)
    mean_p = sum(ps) / len(ps)
    var = sum((x - mean_p) ** 2 for x in ps) / len(ps)
    disagree = math.sqrt(var)
    mean_unc = sum(float(v.uncertainty) for v in usable) / len(usable)
    unc = min(max(mean_unc + 1.4 * disagree, 0.08), 0.95)
    mag = max(v.magnitude_bps for v in usable)
    return ExpertView(
        name="gate",
        p_yes=Decimal(str(round(p, 4))),
        magnitude_bps=mag,
        uncertainty=Decimal(str(round(unc, 4))),
    )
