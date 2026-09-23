"""Latest-frame detection confidence of an InstanceRef."""

from __future__ import annotations

from sih.rules.opdefs._helpers import _latest_matching
from sih.rules.types import EntityRef, OpResult


def confidence(*, entity: EntityRef) -> OpResult:
    """Latest-frame detection confidence of an InstanceRef."""
    ent = _latest_matching(entity)
    if ent is None:
        return OpResult(value=0.0, ok=False)
    return OpResult(value=float(ent.confidence), ok=True)