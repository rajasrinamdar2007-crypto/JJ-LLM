"""Length of a vector."""

from __future__ import annotations

from sih.rules.geometry import Vector
from sih.rules.types import OpResult


def vector_magnitude(*, vector: Vector) -> OpResult:
    return OpResult(value=vector.magnitude(), ok=True)