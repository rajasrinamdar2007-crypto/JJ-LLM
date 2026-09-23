"""Private helpers shared by the rule-operation definitions.

These resolve framebuffer access and per-entity metric extraction; they are
module-internal and not part of the public rule-vocabulary surface.
"""

from __future__ import annotations

from collections.abc import Iterable

from sih.common.types import Entity, FrameEntry
from sih.input.framebuffer import framebuffer
from sih.rules.types import EntityRef, TimeRef

#: Supported per-entity metric names for aggregation ops.
ENTITY_METRICS = frozenset({"confidence", "bbox_area"})


def _entries(window: TimeRef | None) -> list[FrameEntry]:
    """Resolve a TimeRef to a slice of the framebuffer snapshot.

    ``frames: N`` and ``window: N`` both slice the N most recent frames;
    ``offset: N`` selects the single frame N back from the latest.
    """
    snap = framebuffer.snapshot()
    if window is None:
        return snap
    if window.kind in ("frames", "window"):
        return snap[-window.n:] if window.n > 0 else snap
    if window.kind == "offset":
        return snap[-1 - window.n : -window.n] if window.n >= 0 and len(snap) > window.n else []
    return []


def _latest() -> FrameEntry | None:
    return framebuffer.latest()


def _entities_in(entries: Iterable[FrameEntry], ref: EntityRef) -> list[Entity]:
    """Collect every entity matching a ClassRef str or an InstanceRef int."""
    if isinstance(ref, int):
        return [e for entry in entries for e in entry.entities if e.tracker_id == ref]
    return [e for entry in entries for e in entry.entities if e.class_name == ref]


def _latest_matching(ref: EntityRef) -> Entity | None:
    entry = _latest()
    if entry is None:
        return None
    matched = _entities_in([entry], ref)
    return matched[0] if matched else None


def _metric_value(entity: Entity, metric: str) -> float:
    if metric == "confidence":
        return float(entity.confidence)
    if metric == "bbox_area":
        return max(0.0, entity.bbox[2] - entity.bbox[0]) * max(0.0, entity.bbox[3] - entity.bbox[1])
    raise ValueError(f"unknown entity metric: {metric}")