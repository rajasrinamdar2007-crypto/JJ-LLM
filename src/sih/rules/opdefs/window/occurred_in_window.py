"""True when at least one sighting of ``class_name`` occurs in ``window``."""

from __future__ import annotations

from sih.rules.opdefs._helpers import _entries
from sih.rules.types import OpResult, TimeRef


def occurred_in_window(*, class_name: str, window: TimeRef | None = None) -> OpResult:
    """True when at least one sighting of ``class_name`` occurs in ``window``."""
    value = any(e.class_name == class_name for entry in _entries(window) for e in entry.entities)
    return OpResult(value=value, ok=True)