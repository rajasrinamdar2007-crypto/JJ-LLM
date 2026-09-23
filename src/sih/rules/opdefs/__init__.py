"""Individual rule-operation definitions.

Each operation lives in its own module, grouped by category into subfolders,
so a human can review and modify a single op in isolation.  This package
assembles the :data:`OPS` registry — the op-name to implementation mapping the
engine resolves ``OpCall`` arguments through.
"""

from __future__ import annotations

from typing import Any, Callable

from sih.rules.opdefs._helpers import ENTITY_METRICS
from sih.rules.opdefs.geometry.bbox import bbox
from sih.rules.opdefs.geometry.centroid import centroid
from sih.rules.opdefs.geometry.relative_vector import relative_vector
from sih.rules.opdefs.geometry_scalar.box_area import box_area
from sih.rules.opdefs.geometry_scalar.point_distance import point_distance
from sih.rules.opdefs.geometry_scalar.vector_angle import vector_angle
from sih.rules.opdefs.geometry_scalar.vector_magnitude import vector_magnitude
from sih.rules.opdefs.motion.speed import speed
from sih.rules.opdefs.presence.confidence import confidence
from sih.rules.opdefs.presence.is_visible import is_visible
from sih.rules.opdefs.window.avg_in_window import avg_in_window
from sih.rules.opdefs.window.count_in_window import count_in_window
from sih.rules.opdefs.window.occurred_in_window import occurred_in_window
from sih.rules.opdefs.window.persisted_for import persisted_for

OPS: dict[str, Callable[..., Any]] = {
    "is_visible": is_visible,
    "confidence": confidence,
    "occurred_in_window": occurred_in_window,
    "count_in_window": count_in_window,
    "persisted_for": persisted_for,
    "avg_in_window": avg_in_window,
    "speed": speed,
    "bbox": bbox,
    "centroid": centroid,
    "relative_vector": relative_vector,
    "area": box_area,
    "magnitude": vector_magnitude,
    "angle": vector_angle,
    "distance": point_distance,
}

__all__ = [
    "OPS",
    "avg_in_window",
    "bbox",
    "box_area",
    "centroid",
    "confidence",
    "count_in_window",
    "is_visible",
    "occurred_in_window",
    "persisted_for",
    "point_distance",
    "relative_vector",
    "speed",
    "vector_angle",
    "vector_magnitude",
]