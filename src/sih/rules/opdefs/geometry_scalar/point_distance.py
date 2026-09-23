"""Euclidean distance between two points."""

from __future__ import annotations

from sih.rules.geometry import Point
from sih.rules.types import OpResult


def point_distance(*, point_a: Point, point_b: Point) -> OpResult:
    return OpResult(value=point_a.distance_to(point_b), ok=True)