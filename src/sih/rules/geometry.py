"""Geometry primitives used by the rule engine.

Typed values that operations produce and consume: a point, a vector, and a
bounding box.  All three are immutable and JSON-serialisable so they can be
embedded in emitted-event payloads.  The maths helpers in this module mirror
the ``Geometry -> Scalar`` family of operations.
"""

from __future__ import annotations

import math

#: Default sentinel returned when an operation cannot resolve a geometry.
GeometryValue = int | float | bool


def distance(a: "Point", b: "Point") -> float:
    """Euclidean distance between two points."""
    return math.hypot(a.x - b.x, a.y - b.y)


def magnitude(v: "Vector") -> float:
    """Length of a vector."""
    return math.hypot(v.dx, v.dy)


def angle(v: "Vector") -> float:
    """Orientation of a vector in degrees (0..360, counter-clockwise from +x)."""
    return math.degrees(math.atan2(v.dy, v.dx)) % 360.0


class Point:
    """A 2-D point in pixel coordinates."""

    __slots__ = ("x", "y")

    def __init__(self, x: float, y: float) -> None:
        self.x = float(x)
        self.y = float(y)

    def distance_to(self, other: "Point") -> float:
        return distance(self, other)

    def as_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y}

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"Point({self.x:.2f}, {self.y:.2f})"


class Vector:
    """A displacement vector ``dx``, ``dy``."""

    __slots__ = ("dx", "dy")

    def __init__(self, dx: float, dy: float) -> None:
        self.dx = float(dx)
        self.dy = float(dy)

    def magnitude(self) -> float:
        return magnitude(self)

    def angle(self) -> float:
        return angle(self)

    def as_dict(self) -> dict[str, float]:
        return {"dx": self.dx, "dy": self.dy}

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"Vector({self.dx:.2f}, {self.dy:.2f})"


class BoundingBox:
    """An axis-aligned box ``[x1, y1, x2, y2]`` (x2/y2 inclusive)."""

    __slots__ = ("x1", "y1", "x2", "y2")

    def __init__(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.x1 = float(x1)
        self.y1 = float(y1)
        self.x2 = float(x2)
        self.y2 = float(y2)

    def area(self) -> float:
        """Width x height, minimum 0."""
        return max(0.0, (self.x2 - self.x1) * (self.y2 - self.y1))

    def validity(self) -> bool:
        """True when the box is well-formed (x2 > x1 and y2 > y1)."""
        return self.x2 > self.x1 and self.y2 > self.y1

    def as_dict(self) -> dict[str, float]:
        return {"x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2}

    def as_list(self) -> list[float]:
        return [self.x1, self.y1, self.x2, self.y2]

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"BoundingBox({self.x1:.1f}, {self.y1:.1f}, {self.x2:.1f}, {self.y2:.1f})"


__all__ = ["BoundingBox", "Point", "Vector", "angle", "distance", "magnitude"]