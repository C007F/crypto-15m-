from __future__ import annotations

from typing import Protocol

from crypto15m.domain import ExpertView
from crypto15m.perception.features import FeatureFrame


class Expert(Protocol):
    name: str

    def predict(self, features: FeatureFrame) -> ExpertView: ...
