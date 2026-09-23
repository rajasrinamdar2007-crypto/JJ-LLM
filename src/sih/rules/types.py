"""Data types of the rule-engine pipeline.

This module is the *only* place the YAML grammar's static shape is described.
Rules are parsed into a tree of these dataclasses by :mod:`sih.rules.engine`;
evaluation produces typed results.

Two class families live here:

* **AST nodes** — ``Rule``, the ordered ``Step`` definitions, ``Condition``
  (the boolean tree), and ``ValueExpr`` (an operand that resolves to a typed
  value).
* **Runtime results** — carrying the *executed-successfully* boolean that the
  pipeline stores/emits for every evaluated rule step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sih.rules.geometry import BoundingBox, Point, Vector


# ---------------------------------------------------------------------------
# Runtime value types
# ---------------------------------------------------------------------------

#: Entity == ClassRef | InstanceRef.
#: A ClassRef is a class-name `str`; an InstanceRef is a BoT-SORT `tracker_id`
#: int bound to a named variable (e.g. ``$entity``).
EntityRef = str | int

Scalar = int | float | bool
Value = EntityRef | Scalar | Point | Vector | BoundingBox | list[Any] | dict[str, Any]


@dataclass
class TimeRef:
    """``{ frames: N }`` window / ``{ offset: N }`` frame reference.

    ``frames`` slices to the N most recent framebuffer entries, ``offset``
    targets a single entry N steps behind the latest.
    """

    kind: str  # "window" | "offset"
    n: int


@dataclass
class OpResult:
    """Outcome of a single operation call: its value plus success flag.

    ``ok`` is the executed-successfully boolean carried on every result.  An
    operation that cannot resolve its input (e.g. an entity that is not
    visible, an empty window) returns ``ok=False`` with a degenerate value
    rather than raising — the rule keeps running and the flag is what
    downstream steps observe.
    """

    value: Value
    ok: bool

    def truelike(self) -> bool:
        """Interpret this result as a boolean (for condition nodes)."""
        if not self.ok:
            return False
        if isinstance(self.value, bool):
            return self.value
        if isinstance(self.value, (int, float)):
            return self.value != 0
        return bool(self.value)


# ---------------------------------------------------------------------------
# Value expressions (operands)
# ---------------------------------------------------------------------------


@dataclass
class Literal:
    """A literal scalar value from the YAML."""

    value: Scalar | str


@dataclass
class VarRef:
    """Reference to a bound variable (``$entity``)."""

    name: str


@dataclass
class OpCall:
    """Invocation of a rule operation with keyword arguments.

    Each ``args`` value is itself a nested ``ValueExpr`` (literal, var ref, or
    another op call).
    """

    op: str
    args: dict[str, "ValueExpr"] = field(default_factory=dict)


@dataclass
class TimeExpr:
    """A TimeRef literal embedded in an operand position (``{ frames: N }``)."""

    ref: TimeRef


ValueExpr = Literal | VarRef | OpCall | TimeExpr


# ---------------------------------------------------------------------------
# Condition tree
# ---------------------------------------------------------------------------


@dataclass
class Comparison:
    """A binary comparison of two resolved value expressions."""

    operator: str  # gt / gte / lt / lte / eq / ne
    left: ValueExpr
    right: ValueExpr


@dataclass
class Logic:
    """Boolean combinator over the child conditions."""

    op: str  # "all" | "any"
    children: list["Condition"]


@dataclass
class Not:
    """Negation of a single child condition."""

    child: "Condition"


Condition = Comparison | Logic | Not


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


@dataclass
class Emit:
    """A rule action: emit one AnalyticsEvent when its enclosing ``do`` runs.

    ``confidence`` and every ``payload`` value are value expressions resolved
    at emit time against the record's bound variables.
    """

    event_type: str
    confidence: ValueExpr
    payload: dict[str, ValueExpr] = field(default_factory=dict)


@dataclass
class Mark:
    """Set the terminal outcome of a record run (always ``success`` for now)."""

    result: str


@dataclass
class OnExit:
    """Wait until the record's bound entity leaves the frame, then run ``do``.

    ``leave_frames`` is the number of *consecutive* processed frames the entity
    must be absent before the exit is considered real (guards against one-frame
    tracker flicker).
    """

    do: list[Emit | Mark]
    leave_frames: int = 1


@dataclass
class Check:
    """A conditional step: completes the first frame its tree is true.

    A false check never fails the rule — the record keeps tracking and the
    step simply stays pending.  Completion is monotonic once satisfied.
    """

    condition: Condition
    required: bool = True


Step = Check | OnExit


# ---------------------------------------------------------------------------
# Rule
# ---------------------------------------------------------------------------


@dataclass
class Rule:
    """A parsed, validated rule definition (the v2 grammar)."""

    rule_id: str
    class_name: str  # the ClassRef the rule latches instances from
    steps: list[Step]
    description: str = ""
    schema_version: int = 2


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class StepResult:
    """Outcome of a single step evaluation for a record (with executed flag)."""

    ok: bool
    step_name: str  # "check" | "on_exit" | "emit" | "mark"
    detail: str = ""


@dataclass
class EmittedEvent:
    """One event a rule produced, ready for the repository seam.

    Timestamp / GPS from the latest framebuffer entry are attached by the
    rule-engine task; only rule-authored fields live here.
    """

    rule_id: str
    event_type: str
    confidence: float
    payload: dict[str, Any]


@dataclass
class RecordResult:
    """The outcome of tracking a single latched instance across frames."""

    tracker_id: int
    success: bool  # reached an armed on_exit / explicit mark success
    steps: list[StepResult] = field(default_factory=list)
    events: list[EmittedEvent] = field(default_factory=list)


@dataclass
class RuleRunResult:
    """Aggregate result of one rule for one frame tick.

    ``success`` reflects the whole rule run: true when any record reached a
    terminal success during this tick.  ``executed`` captures the
    executed-successfully flag per record/step (present on ``RecordResult``).
    """

    rule_id: str
    success: bool
    records: list[RecordResult] = field(default_factory=list)

    @property
    def events(self) -> list[EmittedEvent]:
        return [event for record in self.records for event in record.events]


__all__ = [
    "Check",
    "Comparison",
    "Condition",
    "Emit",
    "EmittedEvent",
    "EntityRef",
    "Literal",
    "Logic",
    "Mark",
    "Not",
    "OnExit",
    "OpCall",
    "OpResult",
    "RecordResult",
    "Rule",
    "RuleRunResult",
    "Scalar",
    "Step",
    "StepResult",
    "TimeExpr",
    "TimeRef",
    "Value",
    "ValueExpr",
    "VarRef",
]