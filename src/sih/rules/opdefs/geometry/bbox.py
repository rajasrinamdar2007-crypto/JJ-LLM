"""Most recent bounding box of ``entity``."""

from __future__ import annotations

from sih.rules.geometry import BoundingBox
from sih.rules.opdefs._helpers import _latest_matching
from sih.rules.types import EntityRef, OpResult


def bbox(*, entity: EntityRef) -> OpResult:
    """Most recent bounding box of ``entity``."""
    ent = _latest_matching(entity)
    if ent is None:
        return OpResult(value=BoundingBox(0, 0, 0, 0), ok=False)
    box = BoundingBox(*ent.bbox)
    return OpResult(value=box, ok=box.validity())