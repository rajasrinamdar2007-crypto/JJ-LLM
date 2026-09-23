"""Net centroid speed (px/s) of an InstanceRef across the full window."""

from __future__ import annotations

import math

from sih.rules.opdefs._helpers import _entries
from sih.rules.types import EntityRef, OpResult


def speed(*, entity: EntityRef) -> OpResult:
    """Net centroid speed (px/s) of an InstanceRef across the full window."""
    if isinstance(entity, str):
        return OpResult(value=0.0, ok=False)
    row: dict[int, list[tuple[float, float, float]]] = {}
    for entry in _entries(None):
        for e in entry.entities:
            if e.tracker_id == entity:
                cx = (e.bbox[0] + e.bbox[2]) / 2.0
                cy = (e.bbox[1] + e.bbox[3]) / 2.0
                row.setdefault(entity, []).append((entry.ts, cx, cy))
    points = row.get(entity, [])
    points.sort(key=lambda p: p[0])
    if len(points) < 2:
        return OpResult(value=0.0, ok=len(points) == 1)
    dt = points[-1][0] - points[0][0]
    if dt <= 0:
        return OpResult(value=0.0, ok=False)
    dist = math.hypot(points[-1][1] - points[0][1], points[-1][2] - points[0][2])
    return OpResult(value=dist / dt, ok=True)