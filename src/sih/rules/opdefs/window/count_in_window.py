"""Total entity sightings of ``class_name`` within ``window``."""

from __future__ import annotations

from sih.rules.opdefs._helpers import _entries
from sih.rules.types import OpResult, TimeRef


def count_in_window(*, class_name: str, window: TimeRef | None = None) -> OpResult:
    """Total entity sightings of ``class_name`` within ``window``."""
    value = sum(1 for entry in _entries(window) for e in entry.entities if e.class_name == class_name)
    return OpResult(value=float(value), ok=True)