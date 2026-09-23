"""True when ``entity`` is present in the most recent frame entry."""

from __future__ import annotations

from sih.rules.opdefs._helpers import _latest_matching
from sih.rules.types import EntityRef, OpResult


def is_visible(*, entity: EntityRef) -> OpResult:
    """True when ``entity`` is present in the most recent frame entry."""
    return OpResult(value=_latest_matching(entity) is not None, ok=True)