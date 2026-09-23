"""Number of frames (window entries) in which ``entity`` was present."""

from __future__ import annotations

from sih.rules.opdefs._helpers import _entities_in, _entries
from sih.rules.types import EntityRef, OpResult, TimeRef


def persisted_for(*, entity: EntityRef, window: TimeRef | None = None) -> OpResult:
    """Number of frames (window entries) in which ``entity`` was present.

    This is the "same ENTITY appears across multiple frames" check — the value
    counts sightings of one InstanceRef (tracker_id) or one ClassRef.
    """
    value = sum(1 for _ in _entities_in(_entries(window), entity))
    return OpResult(value=float(value), ok=True)