"""Generate a strict, machine-accurate rule grammar spec for the LLM prompt."""

from __future__ import annotations

import inspect
from typing import Any

from sih.rules.ops import OPS

_COMPARISON_OPS = ("lt", "lte", "gt", "gte", "eq", "ne")

_ENTITY_PARAMS = frozenset({"entity", "entity_a", "entity_b"})
_CLASS_PARAMS = frozenset({"class_name"})
_METRIC_PARAMS = frozenset({"metric"})
_GEOMETRY_PARAMS = frozenset({"box", "vector", "point_a", "point_b"})


def _param_kind(name: str, annotation: Any, has_default: bool) -> str:
    if name in _ENTITY_PARAMS or "EntityRef" in str(annotation):
        return "entity"
    if name in _CLASS_PARAMS:
        return "class"
    if name in _METRIC_PARAMS or annotation is str:
        return "str"
    if "TimeRef" in str(annotation):
        return "timeref"
    if "BoundingBox" in str(annotation) or name in _GEOMETRY_PARAMS:
        return "geometry"
    if "Point" in str(annotation) or "Vector" in str(annotation):
        return "geometry"
    if "bool" in str(annotation):
        return "bool"
    return "value"


def op_signatures() -> str:
    """Render one line per op with its arg kinds and required-ness."""
    lines: list[str] = []
    for name in sorted(OPS):
        fn = OPS[name]
        sig = inspect.signature(fn)
        parts: list[str] = []
        for pname, param in sig.parameters.items():
            kind = _param_kind(
                pname, param.annotation, param.default is not inspect.Parameter.empty
            )
            marker = "" if param.default is inspect.Parameter.empty else "?"
            parts.append(f"{pname}={kind}{marker}")
        lines.append(f"- {name}({', '.join(parts)})")
    return "\n".join(lines)


def _value_expr_grammar() -> str:
    return """Value expressions (VALUE):
- literal number / string / bool
- $var reference (the tracker binds $entity)
- { frames: N } / { offset: N } TimeRef (N is a non-negative int)
- { op: NAME, <args> } operation call"""


def _condition_grammar() -> str:
    comparisons = ", ".join(_COMPARISON_OPS)
    return f"""Condition nodes are single-key mappings; no free strings allowed.
- Comparisons ({comparisons}): {{ gt: {{ left: VALUE, right: VALUE }} }}
- Logic: {{ all: [COND, ...] }}, {{ any: [COND, ...] }}, {{ not: COND }}"""


def _on_exit_grammar() -> str:
    return """on_exit step:
  { on_exit: { do: [ACTION, ...], leave_frames: N } }
- do is a non-empty list; each ACTION is emit or mark.
- emit: { emit: { event_type: STRING, confidence: VALUE?, payload: { KEY: VALUE, ... }? } }
- mark: { mark: success }"""


def build_grammar_spec() -> str:
    """Return the full strict grammar description used in the system prompt."""
    return f"""Rule file grammar. schema_version MUST be 2.
Top-level keys allowed: schema_version, rule_id, description, track, steps.
- rule_id: non-empty string; MUST equal the filename stem.
- track: exactly {{ class: CLASS_NAME }}; CLASS_NAME MUST be from the allowed classes list.
- steps: non-empty list; each step is check or on_exit.

Condition grammar:
{_condition_grammar()}

{_value_expr_grammar()}

Operations (arg=entity/class/str/timeref/geometry, ? = optional):
{op_signatures()}

on_exit grammar:
{_on_exit_grammar()}

Constraints:
- is_visible / confidence / bbox / centroid / speed / persisted_for / avg_in_window take an entity (use $entity).
- count_in_window / occurred_in_window take a class name from the allowed classes list.
- area / magnitude / angle / distance take geometry values from bbox / centroid / relative_vector op calls.
- leave_frames must be a positive int (default 1).
- emit confidence and payload values are VALUE expressions.
- Only the canonical ops, condition operators, and step kinds listed above exist; inventing new ones is an error."""