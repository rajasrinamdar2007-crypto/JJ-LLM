"""Orientation of a vector in degrees (0..360, counter-clockwise from +x)."""

from __future__ import annotations

from sih.rules.geometry import Vector
from sih.rules.types import OpResult


def vector_angle(*, vector: Vector) -> OpResult:
    return OpResult(value=vector.angle(), ok=True)