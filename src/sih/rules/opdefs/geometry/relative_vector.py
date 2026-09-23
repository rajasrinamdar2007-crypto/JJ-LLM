"""Vector from ``entity_a``'s centroid to ``entity_b``'s centroid."""

from __future__ import annotations

from sih.rules.geometry import Vector
from sih.rules.opdefs._helpers import _latest_matching
from sih.rules.types import EntityRef, OpResult


def relative_vector(*, entity_a: EntityRef, entity_b: EntityRef) -> OpResult:
    """Vector from ``entity_a``'s centroid to ``entity_b``'s centroid."""
    a = _latest_matching(entity_a)
    b = _latest_matching(entity_b)
    if a is None or b is None:
        return OpResult(value=Vector(0.0, 0.0), ok=False)
    acx = (a.bbox[0] + a.bbox[2]) / 2.0
    acy = (a.bbox[1] + a.bbox[3]) / 2.0
    bcx = (b.bbox[0] + b.bbox[2]) / 2.0
    bcy = (b.bbox[1] + b.bbox[3]) / 2.0
    return OpResult(value=Vector(bcx - acx, bcy - acy), ok=True)