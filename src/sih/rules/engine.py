"""Rule engine: YAML parsing, validation, and the stateful frame tick.

Grammar (one rule per file, schema ``schema_version: 2``)::

    schema_version: 2
    rule_id: pothole_persist_conf
    description: ...
    track:
      class: pothole
    steps:
      - check:
          gte:
            left: { op: persisted_for, entity: "$entity", window: { frames: 5 } }
            right: 5
      - on_exit:
          leave_frames: 1
          do:
            - emit:
                event_type: pothole_confirmed
                confidence: { op: avg_in_window, entity: "$entity", metric: confidence, window: { frames: 5 } }
                payload: { frames_seen: { op: persisted_for, entity: "$entity", window: { frames: 5 } } }
            - mark: success

Semantics
---------
A rule is a stateful tracker: it latches every new BoT-SORT ``tracker_id`` of
``track.class`` and advances one ``_Record`` per instance:

* ``check`` steps are latched-mustards — they complete the first frame their
  condition tree is true; a false check never fails the rule, the record just
  keeps tracking.  Completion is monotonic.
* ``on_exit`` fires once per record when the latched entity has been absent
  for ``leave_frames`` consecutive processed frames **and** all ``check``
  steps completed (the record is "armed").  It runs its ``do`` list — ``emit``
  produces an :class:`EmittedEvent`, ``mark: success`` sets the terminal
  outcome.  A record that exits before arming is dropped silently.

Input is strictly validated at load time (unknown keys/ops/operators raise);
rules are expected to be pre-validated upstream, so runtime resolution
failures surface as ``ok=False`` results rather than exceptions.
"""

from __future__ import annotations

import logging
import operator
from pathlib import Path

import yaml
from typing import Any, Callable

from sih.input.framebuffer import framebuffer
from sih.rules.geometry import BoundingBox, Point, Vector
from sih.rules.ops import OPS
from sih.rules.types import (
    Check,
    Comparison,
    Condition,
    Emit,
    EmittedEvent,
    Literal,
    Logic,
    Mark,
    Not,
    OnExit,
    OpCall,
    OpResult,
    RecordResult,
    Rule,
    RuleRunResult,
    Step,
    StepResult,
    TimeExpr,
    TimeRef,
    ValueExpr,
    VarRef,
)

logger = logging.getLogger(__name__)

#: Allowed condition-tree operators (the boolean + comparison family).
CONDITION_OPERATORS = frozenset({"all", "any", "not", "gt", "gte", "lt", "lte", "eq", "ne"})

#: Binary comparison predicates keyed by YAML operator name.
_COMPARATORS: dict[str, Callable[[Any, Any], bool]] = {
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
    "eq": operator.eq,
    "ne": operator.ne,
}


class RuleSyntaxError(ValueError):
    """Raised when a rule file violates the v2 grammar."""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _syntax(msg: str, path: Path | None = None) -> RuleSyntaxError:
    where = f" in {path}" if path is not None else ""
    return RuleSyntaxError(f"{msg}{where}")


def parse_value(raw: Any, path: Path | None = None) -> ValueExpr:
    """Parse a value expression: literal, ``$var`` ref, ``{ frames: N }``, or ``{ op: ... }`` call."""
    if isinstance(raw, (int, float, bool)):
        return Literal(raw)
    if isinstance(raw, str):
        if raw.startswith("$"):
            return VarRef(raw[1:])
        return Literal(raw)
    if isinstance(raw, dict):
        if "op" in raw:
            args = raw.copy()
            args.pop("op")
            if len(args) != len(raw) - 1:
                raise _syntax(f"op call cannot carry duplicate keys: {raw!r}", path)
            op_name = str(raw["op"])
            if op_name not in OPS:
                raise _syntax(f"unknown operation {op_name!r} (available: {sorted(OPS)})", path)
            return OpCall(op_name, {k: parse_value(v, path) for k, v in args.items()})
        ref = parse_time_ref(raw, path)
        if ref is not None:
            return TimeExpr(ref=ref)
        raise _syntax(f"value expression must be literal, $var, or {{op: ...}}: {raw!r}", path)
    raise _syntax(f"unsupported value expression: {raw!r}", path)


def parse_time_ref(raw: Any, path: Path | None = None) -> TimeRef | None:
    """Parse ``{ frames: N }`` / ``{ offset: N }``; None for the full window."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or not raw:
        raise _syntax(f"TimeRef must be a {{frames: N}} or {{offset: N}} mapping, got {raw!r}", path)
    if len(raw) != 1 or next(iter(raw)) not in {"frames", "offset"}:
        raise _syntax(f"TimeRef supports only 'frames'/'offset', got {raw!r}", path)
    (kind, n) = next(iter(raw.items()))
    if not isinstance(n, int) or n < 0:
        raise _syntax(f"TimeRef {kind} must be a non-negative int, got {n!r}", path)
    return TimeRef(kind=kind, n=n)


def parse_condition(raw: Any, path: Path | None = None) -> Condition:
    """Parse the boolean/comparison condition tree (tree-only, no free strings)."""
    if not isinstance(raw, dict) or len(raw) != 1:
        raise _syntax(f"condition node must be a single-key mapping, got {raw!r}", path)
    (key, val) = next(iter(raw.items()))
    if key not in CONDITION_OPERATORS:
        raise _syntax(f"unknown condition operator {key!r} (allowed: {sorted(CONDITION_OPERATORS)})", path)

    if key in {"all", "any"}:
        if not isinstance(val, list):
            raise _syntax(f"{key} expects a list of conditions, got {val!r}", path)
        return Logic(op=key, children=[parse_condition(c, path) for c in val])

    if key == "not":
        return Not(child=parse_condition(val, path))

    if not isinstance(val, dict) or set(val) != {"left", "right"}:
        raise _syntax(f"{key} expects {{left: ..., right: ...}}, got {val!r}", path)
    return Comparison(
        operator=key,
        left=parse_value(val["left"], path),
        right=parse_value(val["right"], path),
    )


def parse_emit(raw: Any, path: Path | None = None) -> Emit:
    if not isinstance(raw, dict):
        raise _syntax(f"emit expects a mapping, got {raw!r}", path)
    unknown = set(raw) - {"event_type", "confidence", "payload"}
    if unknown:
        raise _syntax(f"emit: unknown keys {sorted(unknown)}", path)
    event_type = raw.get("event_type")
    if not isinstance(event_type, str) or not event_type:
        raise _syntax(f"emit requires a non-empty event_type, got {event_type!r}", path)
    raw_payload = raw.get("payload", {})
    if not isinstance(raw_payload, dict):
        raise _syntax(f"emit payload must be a mapping, got {raw_payload!r}", path)
    return Emit(
        event_type=event_type,
        confidence=parse_value(raw.get("confidence", 1.0), path),
        payload={k: parse_value(v, path) for k, v in raw_payload.items()},
    )


def parse_on_exit(raw: Any, path: Path | None = None) -> OnExit:
    if not isinstance(raw, dict):
        raise _syntax(f"on_exit expects a mapping, got {raw!r}", path)
    unknown = set(raw) - {"do", "leave_frames"}
    if unknown:
        raise _syntax(f"on_exit: unknown keys {sorted(unknown)}", path)
    raw_do = raw.get("do")
    if not isinstance(raw_do, list) or not raw_do:
        raise _syntax("on_exit requires a non-empty 'do' list", path)

    do: list[Emit | Mark] = []
    for item in raw_do:
        if not isinstance(item, dict) or len(item) != 1:
            raise _syntax(f"on_exit action must be a single-key mapping, got {item!r}", path)
        (action, val) = next(iter(item.items()))
        if action == "emit":
            do.append(parse_emit(val, path))
        elif action == "mark":
            if val != "success":
                raise _syntax(f"mark supports only 'success', got {val!r}", path)
            do.append(Mark(result="success"))
        else:
            raise _syntax(f"unknown on_exit action {action!r} (allowed: emit, mark)", path)

    raw_leave = raw.get("leave_frames", 1)
    if not isinstance(raw_leave, int) or raw_leave < 1:
        raise _syntax(f"leave_frames must be a positive int, got {raw_leave!r}", path)
    return OnExit(do=do, leave_frames=raw_leave)


def parse_step(raw: Any, path: Path | None = None) -> Step:
    if not isinstance(raw, dict) or len(raw) != 1:
        raise _syntax(f"step must be a single-key mapping, got {raw!r}", path)
    (kind, val) = next(iter(raw.items()))
    if kind == "check":
        return Check(condition=parse_condition(val, path))
    if kind == "on_exit":
        return parse_on_exit(val, path)
    raise _syntax(f"unknown step type {kind!r} (allowed: check, on_exit)", path)


def parse_rule(raw: dict[str, Any], path: Path | None = None) -> Rule:
    """Parse and strictly validate one rule definition."""
    if not isinstance(raw, dict):
        raise _syntax(f"rule file must be a mapping, got {raw!r}", path)

    unknown = set(raw) - {"schema_version", "rule_id", "description", "track", "steps"}
    if unknown:
        raise _syntax(f"rule: unknown keys {sorted(unknown)}", path)

    schema = raw.get("schema_version")
    if schema != 2:
        raise _syntax(f"unsupported schema_version {schema!r} (expected 2)", path)

    rule_id = raw.get("rule_id")
    if not isinstance(rule_id, str) or not rule_id:
        raise _syntax(f"rule requires a non-empty rule_id, got {rule_id!r}", path)

    track = raw.get("track")
    if not isinstance(track, dict) or set(track) != {"class"}:
        raise _syntax(f"track must be {{class: ...}}, got {track!r}", path)
    class_name = track["class"]
    if not isinstance(class_name, str) or not class_name:
        raise _syntax(f"track.class must be a non-empty class name, got {class_name!r}", path)

    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise _syntax("rule requires a non-empty 'steps' list", path)

    steps = [parse_step(s, path) for s in steps_raw]

    description = raw.get("description", "")
    if not isinstance(description, str):
        raise _syntax(f"description must be a string, got {description!r}", path)

    return Rule(
        rule_id=rule_id,
        class_name=class_name,
        steps=steps,
        description=description,
        schema_version=schema,
    )


def load_rule_file(path: Path) -> Rule:
    """Load and validate a single rule YAML file."""
    with open(path, "r", encoding="utf-8") as fh:
        raw: Any = yaml.safe_load(fh)
    if raw is None:
        raise _syntax("empty rule file", path)
    rule = parse_rule(raw, path)
    if rule.rule_id != path.stem:
        raise _syntax(f"rule_id {rule.rule_id!r} does not match filename {path.stem!r}", path)
    return rule


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    """Convert a resolved value into a JSON-serialisable form."""
    if isinstance(value, Point):
        return value.as_dict()
    if isinstance(value, Vector):
        return value.as_dict()
    if isinstance(value, BoundingBox):
        return value.as_dict()
    return value


def resolve(expr: ValueExpr, scope: dict[str, Any]) -> OpResult:
    """Resolve a value expression against a variable scope."""
    if isinstance(expr, Literal):
        return OpResult(value=expr.value, ok=True)
    if isinstance(expr, VarRef):
        if expr.name not in scope:
            return OpResult(value=None, ok=False)
        return OpResult(value=scope[expr.name], ok=True)
    if isinstance(expr, TimeExpr):
        return OpResult(value=expr.ref, ok=True)

    op_fn = OPS.get(expr.op)
    if op_fn is None:
        return OpResult(value=None, ok=False)

    kwargs: dict[str, Any] = {}
    for name, arg in expr.args.items():
        res = resolve(arg, scope)
        if not res.ok:
            return OpResult(value=None, ok=False)
        kwargs[name] = res.value
    return op_fn(**kwargs)


def evaluate(condition: Condition, scope: dict[str, Any]) -> OpResult:
    """Evaluate the condition tree; returns an ``OpResult`` whose value is bool."""
    if isinstance(condition, Comparison):
        left = resolve(condition.left, scope)
        right = resolve(condition.right, scope)
        if not (left.ok and right.ok):
            return OpResult(value=False, ok=False)
        try:
            value = _COMPARATORS[condition.operator](left.value, right.value)
        except TypeError:
            return OpResult(value=False, ok=False)
        return OpResult(value=bool(value), ok=True)

    if isinstance(condition, Logic):
        children = [evaluate(c, scope) for c in condition.children]
        if condition.op == "all":
            return OpResult(value=all(c.truelike() for c in children), ok=all(c.ok for c in children))
        return OpResult(value=any(c.truelike() for c in children), ok=any(c.ok for c in children))

    if isinstance(condition, Not):
        child = evaluate(condition.child, scope)
        return OpResult(value=not child.truelike(), ok=child.ok)

    raise TypeError(f"unknown condition type: {type(condition).__name__}")


def _emit_event(emit: Emit, rule_id: str, scope: dict[str, Any]) -> EmittedEvent | None:
    """Build an EmittedEvent; returns None when any payload value fails to resolve."""
    conf = resolve(emit.confidence, scope)
    if not conf.ok:
        return None
    payload: dict[str, Any] = {}
    for key, expr in emit.payload.items():
        res = resolve(expr, scope)
        if not res.ok:
            return None
        payload[key] = _jsonable(res.value)
    return EmittedEvent(
        rule_id=rule_id,
        event_type=emit.event_type,
        confidence=float(conf.value),
        payload=payload,
    )


class _Record:
    """Per-instance state for one tracked entity within a rule."""

    __slots__ = ("tracker_id", "completed", "absent", "steps", "events", "success", "done")

    def __init__(self, tracker_id: int) -> None:
        self.tracker_id = tracker_id
        #: Indexes of `check` steps that have completed (monotonic latch).
        self.completed: set[int] = set()
        self.absent: int = 0
        self.steps: list[StepResult] = []
        self.events: list[EmittedEvent] = []
        self.success: bool = False
        self.done: bool = False


class RuleRegistry:
    """Holds loaded rules and advances their per-instance state per frame."""

    def __init__(self) -> None:
        self._rules: list[Rule] = []
        self._by_id: dict[str, Rule] = {}
        self._records: dict[str, dict[int, _Record]] = {}

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @property
    def rules(self) -> list[Rule]:
        return list(self._rules)

    def load_dir(self, dir_path: str | Path) -> None:
        """Load and validate every ``*.yaml`` rule in a directory."""
        p = Path(dir_path)
        self._rules = []
        self._by_id = {}
        self._records = {}
        if not p.is_dir():
            logger.warning("rules dir %s not found; no rules loaded", p)
            return
        for path in sorted(p.glob("*.yaml")):
            rule = load_rule_file(path)
            if rule.rule_id in self._by_id:
                raise RuleSyntaxError(f"duplicate rule_id {rule.rule_id!r} from {path}")
            self._by_id[rule.rule_id] = rule
            self._records[rule.rule_id] = {}
            self._rules.append(rule)
            logger.info("loaded rule %s tracking class %r (%d steps)", rule.rule_id, rule.class_name, len(rule.steps))

    # ------------------------------------------------------------------
    # Frame tick
    # ------------------------------------------------------------------

    def tick(self) -> list[RuleRunResult]:
        """Advance every rule by one processed frame; return this tick's results."""
        return [self._tick_rule(rule) for rule in self._rules]

    def _tick_rule(self, rule: Rule) -> RuleRunResult:
        latest = framebuffer.latest()
        if latest is None:
            return RuleRunResult(rule_id=rule.rule_id, success=False)

        present = {
            e.tracker_id
            for e in latest.entities
            if e.class_name == rule.class_name
        }
        records = self._records[rule.rule_id]

        # Latch newly-seen instances of the class.
        for tracker_id in present:
            records.setdefault(tracker_id, _Record(tracker_id))

        check_indices = [i for i, step in enumerate(rule.steps) if isinstance(step, Check)]
        terminal: list[RecordResult] = []

        for tracker_id, rec in list(records.items()):
            visible = tracker_id in present
            rec.absent = 0 if visible else rec.absent + 1

            if not rec.done and (not visible) and rec.absent >= self._exit_after(rule):
                rec.done = True
                armed = check_indices and all(i in rec.completed for i in check_indices) or not check_indices
                if armed:
                    self._run_exit(rule, rec)
                    terminal.append(
                        RecordResult(
                            tracker_id=tracker_id,
                            success=rec.success,
                            steps=rec.steps,
                            events=rec.events,
                        )
                    )
                del records[tracker_id]
                continue

            # Keep tracking: evaluate not-yet-completed check steps.
            for i in check_indices:
                if i in rec.completed:
                    continue
                step = rule.steps[i]
                assert isinstance(step, Check)
                scope = {"entity": tracker_id}
                res = evaluate(step.condition, scope)
                if res.truelike():
                    rec.completed.add(i)
                    rec.steps.append(
                        StepResult(ok=True, step_name="check", detail=f"step {i} satisfied at tracker {tracker_id}")
                    )

        return RuleRunResult(
            rule_id=rule.rule_id,
            success=any(r.success for r in terminal),
            records=terminal,
        )

    @staticmethod
    def _exit_after(rule: Rule) -> int:
        for step in rule.steps:
            if isinstance(step, OnExit):
                return step.leave_frames
        return 1

    def _run_exit(self, rule: Rule, rec: _Record) -> None:
        """Run the record's on_exit ``do`` list (emit / mark actions)."""
        scope = {"entity": rec.tracker_id}
        for step in rule.steps:
            if not isinstance(step, OnExit):
                continue
            rec.steps.append(StepResult(ok=True, step_name="on_exit", detail=f"entity {rec.tracker_id} left view"))
            for action in step.do:
                if isinstance(action, Emit):
                    event = _emit_event(action, rule.rule_id, scope)
                    if event is None:
                        rec.steps.append(
                            StepResult(ok=False, step_name="emit", detail=f"emit {action.event_type} could not resolve")
                        )
                    else:
                        rec.steps.append(
                            StepResult(ok=True, step_name="emit", detail=f"emitted {action.event_type}")
                        )
                        rec.events.append(event)
                elif isinstance(action, Mark):
                    rec.success = action.result == "success"
                    rec.steps.append(StepResult(ok=True, step_name="mark", detail=action.result))


__all__ = ["RuleRegistry", "RuleSyntaxError", "evaluate", "load_rule_file", "parse_rule"]