"""Area of a bounding box (width × height, minimum 0)."""

from __future__ import annotations

from sih.rules.geometry import BoundingBox
from sih.rules.types import OpResult


def box_area(*, box: BoundingBox) -> OpResult:
    return OpResult(value=box.area(), ok=True)