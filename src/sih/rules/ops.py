"""Rule operation registry — backward-compatible re-exports.

The operation implementations now live one-per-file under
:mod:`sih.rules.opdefs`, grouped into category subfolders.  This module keeps
the public surface stable for existing importers (the engine, the rule-engine
task, and tests): ``OPS`` and every operation remain importable from
``sih.rules.ops`` exactly as before.
"""

from __future__ import annotations

from sih.rules.opdefs import (
    OPS,
    avg_in_window,
    bbox,
    box_area,
    centroid,
    confidence,
    count_in_window,
    is_visible,
    occurred_in_window,
    persisted_for,
    point_distance,
    relative_vector,
    speed,
    vector_angle,
    vector_magnitude,
)

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