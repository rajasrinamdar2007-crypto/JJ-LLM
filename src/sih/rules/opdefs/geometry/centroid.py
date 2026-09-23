"""Most recent bounding-box centre of ``entity``."""

from __future__ import annotations

from sih.rules.geometry import Point
from sih.rules.opdefs._helpers import _latest_matching
from sih.rules.types import EntityRef, OpResult


def centroid(*, entity: EntityRef) -> OpResult:
    """Most recent bounding-box centre of ``entity``."""
    ent = _latest_matching(entity)
    if ent is None:
        return OpResult(value=Point(0.0, 0.0), ok=False)
    return OpResult(value=Point((ent.bbox[0] + ent.bbox[2]) / 2.0, (ent.bbox[1] + ent.bbox[3]) / 2.0), ok=True)