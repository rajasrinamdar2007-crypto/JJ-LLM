"""Mean of ``metric`` over the entries where ``entity`` was present."""

from __future__ import annotations

from sih.rules.opdefs._helpers import ENTITY_METRICS, _entities_in, _entries, _metric_value
from sih.rules.types import EntityRef, OpResult, TimeRef


def avg_in_window(*, entity: EntityRef, metric: str, window: TimeRef | None = None) -> OpResult:
    """Mean of ``metric`` over the entries where ``entity`` was present.

    Frames where the entity was absent are not counted (no zero filling); the
    mean runs up to the entity's last occurrence in the window.
    """
    if metric not in ENTITY_METRICS:
        return OpResult(value=0.0, ok=False)
    values = [_metric_value(e, metric) for e in _entities_in(_entries(window), entity)]
    if not values:
        return OpResult(value=0.0, ok=False)
    return OpResult(value=sum(values) / len(values), ok=True)