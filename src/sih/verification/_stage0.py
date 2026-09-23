"""Stage 0 -- structural validation of a schema-v2 rule.

The engine (:mod:`sih.rules.engine`) already performs strict load-time
validation and raises :class:`RuleSyntaxError`.  This stage reuses that parser
as-is (``parse_rule``) to build the authoritative :class:`Rule` AST, but wraps
it with two additional responsibilities the engine deliberately leaves out:

* every failure is attributed to a precise structural path (e.g.
  ``steps[0].check.gte.left.op``) so an LLM retry loop knows exactly which
  node to fix, and
* op-call **argument** checks -- unknown kwarg names, missing required
  arguments, and statically-untypeable argument kinds -- are verified against
  the real ``OPS`` signatures via :func:`inspect.signature`.  The engine only
  checks that an op *name* exists; a misspelled or wrong-shaped kwarg would
  otherwise slip through and crash at runtime.

The prefix-free paths mirror the YAML shape: ``steps[i]``, ``.check`` /
``.on_exit``, condition operator keys (``gte``, ``all[j]``, ``not``, ...),
``.left`` / ``.right``, and op-call components ``.op`` / ``.<argname>``.

``parse_rule`` is invoked only after the walker reports zero errors; both are
kept so the validator can never drift from the engine's real grammar.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from sih.rules.engine import (
    CONDITION_OPERATORS,
    OPS,
    RuleSyntaxError,
    parse_rule,
)
from sih.rules.types import Rule
from sih.verification._errors import (
    STAGE_STRUCTURAL,
    VerificationError,
)

_VALID_TOP_KEYS = frozenset({"schema_version", "rule_id", "description", "track", "steps"})
_VALID_STEP_KINDS = frozenset({"check", "on_exit"})
_VALID_ON_EXIT_KEYS = frozenset({"do", "leave_frames"})
_VALID_EMIT_KEYS = frozenset({"event_type", "confidence", "payload"})
_VALID_TIME_REF_KEYS = frozenset({"frames", "offset"})

_MISSING = inspect.Parameter.empty


def _err(path: str, message: str) -> VerificationError:
    return VerificationError(stage=STAGE_STRUCTURAL, message=message, path=path)


# ---------------------------------------------------------------------------
# Op-argument validation (the engine does not do this)
# ---------------------------------------------------------------------------


def _annotation_kind(annotation: str) -> str:
    """Coarse expected-kind for an op parameter, from its string annotation."""
    if "EntityRef" in annotation:
        return "entity"
    if "TimeRef" in annotation:
        return "timeref"
    if any(t in annotation for t in ("BoundingBox", "Point", "Vector")):
        return "geometry"
    if "bool" in annotation:
        return "bool"
    if "int" in annotation:
        return "int"
    if "float" in annotation:
        return "float"
    if "str" in annotation:
        return "str"
    return "any"


def _static_kind(raw: Any) -> str:
    """Coarse static kind of a raw YAML value-expression."""
    if isinstance(raw, bool):
        return "bool"
    if isinstance(raw, int):
        return "int"
    if isinstance(raw, float):
        return "float"
    if isinstance(raw, str):
        return "var" if raw.startswith("$") else "str"
    if isinstance(raw, dict):
        return "op" if "op" in raw else "timeref"
    return "other"


#: Which static kinds a parameter of a given expected-kind may receive.
_ALLOWED_KINDS: dict[str, set[str]] = {
    "entity": {"int", "str", "var", "op"},
    "str": {"str"},
    "timeref": {"timeref"},
    "geometry": {"op"},
    "bool": {"bool"},
    "int": {"int"},
    "float": {"int", "float"},
    "any": {"int", "float", "bool", "str", "var", "op", "timeref"},
}


def _check_op_call(raw: dict[str, Any], path: str, errs: list[VerificationError]) -> None:
    """Validate one ``{op: name, ...}`` node's name and its keyword arguments."""
    op_name = raw.get("op")
    if not isinstance(op_name, str) or op_name not in OPS:
        errs.append(
            _err(
                f"{path}.op",
                f"unknown operation {op_name!r} (available: {sorted(OPS)})",
            )
        )
        return

    fn = OPS[op_name]
    sig = inspect.signature(fn)
    params = sig.parameters
    valid = set(params)
    args = {k: v for k, v in raw.items() if k != "op"}

    for name in sorted(set(args) - valid):
        errs.append(
            _err(
                f"{path}.{name}",
                f"unknown argument {name!r} for operation {op_name!r} "
                f"(valid arguments: {sorted(valid)})",
            )
        )

    missing = sorted(
        n for n, p in params.items() if p.default is _MISSING and n not in args
    )
    if missing:
        errs.append(
            _err(
                path,
                f"operation {op_name!r} is missing required argument(s): "
                f"{missing} (valid arguments: {sorted(valid)})",
            )
        )

    for name, rawval in args.items():
        param = params.get(name)
        if param is None:
            continue  # already reported as unknown argument
        expected = _annotation_kind(str(param.annotation))
        got = _static_kind(rawval)
        if got not in _ALLOWED_KINDS.get(expected, _ALLOWED_KINDS["any"]):
            expect_desc = {
                "entity": "an entity reference ($entity or a class name string)",
                "str": "a literal string",
                "timeref": "a {frames: N} / {offset: N} mapping",
                "geometry": "a geometry value produced by an op call (bbox / centroid / relative_vector)",
                "bool": "a literal true / false",
                "int": "an integer literal",
                "float": "a numeric literal",
            }.get(expected, "a compatible value")
            errs.append(
                _err(
                    f"{path}.{name}",
                    f"argument {name!r} of operation {op_name!r} expects "
                    f"{expect_desc}, got {rawval!r}",
                )
            )


# ---------------------------------------------------------------------------
# Recursive structural walker (pathed validation)
# ---------------------------------------------------------------------------


def _validate_value(raw: Any, path: str, errs: list[VerificationError]) -> None:
    """Validate a value expression position (literal / $var / op / time ref)."""
    if isinstance(raw, (bool, int, float)) or isinstance(raw, str):
        return
    if not isinstance(raw, dict):
        errs.append(_err(path, f"unsupported value expression: {raw!r}"))
        return
    if "op" in raw:
        _check_op_call(raw, path, errs)
        for name, argval in raw.items():
            if name == "op":
                continue
            _validate_value(argval, f"{path}.{name}", errs)
        return
    if len(raw) != 1 or next(iter(raw)) not in _VALID_TIME_REF_KEYS:
        errs.append(
            _err(path, f"expected a {{frames: N}} / {{offset: N}} mapping or an {{op: ...}} call, got {raw!r}")
        )
        return
    (kind, n) = next(iter(raw.items()))
    if not isinstance(n, int) or n < 0:
        errs.append(_err(f"{path}.{kind}", f"TimeRef {kind} must be a non-negative int, got {n!r}"))


def _validate_condition(raw: Any, path: str, errs: list[VerificationError]) -> None:
    if not isinstance(raw, dict) or len(raw) != 1:
        errs.append(_err(path, f"condition node must be a single-key mapping, got {raw!r}"))
        return
    (key, val) = next(iter(raw.items()))
    if key not in CONDITION_OPERATORS:
        errs.append(
            _err(path, f"unknown condition operator {key!r} (allowed: {sorted(CONDITION_OPERATORS)})")
        )
        return
    if key in {"all", "any"}:
        if not isinstance(val, list):
            errs.append(_err(f"{path}.{key}", f"{key} expects a list of conditions, got {val!r}"))
            return
        for i, child in enumerate(val):
            _validate_condition(child, f"{path}.{key}[{i}]", errs)
        return
    if key == "not":
        _validate_condition(val, f"{path}.not", errs)
        return
    if not isinstance(val, dict) or set(val) != {"left", "right"}:
        errs.append(_err(f"{path}.{key}", f"{key} expects {{left: ..., right: ...}}, got {val!r}"))
        return
    _validate_value(val["left"], f"{path}.{key}.left", errs)
    _validate_value(val["right"], f"{path}.{key}.right", errs)


def _validate_emit(raw: Any, path: str, errs: list[VerificationError]) -> None:
    if not isinstance(raw, dict):
        errs.append(_err(path, f"emit expects a mapping, got {raw!r}"))
        return
    for key in sorted(set(raw) - _VALID_EMIT_KEYS):
        errs.append(_err(f"{path}.{key}", f"emit: unknown key {key!r} (valid: {sorted(_VALID_EMIT_KEYS)})"))
    event_type = raw.get("event_type")
    if not isinstance(event_type, str) or not event_type:
        errs.append(_err(f"{path}.event_type", f"emit requires a non-empty event_type, got {event_type!r}"))
    if "confidence" in raw:
        _validate_value(raw["confidence"], f"{path}.confidence", errs)
    payload = raw.get("payload", {})
    if not isinstance(payload, dict):
        errs.append(_err(f"{path}.payload", f"emit payload must be a mapping, got {payload!r}"))
    else:
        for key, value in payload.items():
            _validate_value(value, f"{path}.payload.{key}", errs)


def _validate_action(raw: Any, path: str, errs: list[VerificationError]) -> None:
    if not isinstance(raw, dict) or len(raw) != 1:
        errs.append(_err(path, f"on_exit action must be a single-key mapping, got {raw!r}"))
        return
    (action, val) = next(iter(raw.items()))
    if action == "emit":
        _validate_emit(val, f"{path}.emit", errs)
    elif action == "mark":
        if val != "success":
            errs.append(_err(f"{path}.mark", f"mark supports only 'success', got {val!r}"))
    else:
        errs.append(_err(f"{path}.{action}", f"unknown on_exit action {action!r} (allowed: emit, mark)"))


def _validate_on_exit(raw: Any, path: str, errs: list[VerificationError]) -> None:
    if not isinstance(raw, dict):
        errs.append(_err(path, f"on_exit expects a mapping, got {raw!r}"))
        return
    for key in sorted(set(raw) - _VALID_ON_EXIT_KEYS):
        errs.append(_err(f"{path}.{key}", f"on_exit: unknown key {key!r} (valid: {sorted(_VALID_ON_EXIT_KEYS)})"))
    do = raw.get("do")
    if not isinstance(do, list) or not do:
        errs.append(_err(f"{path}.do", "on_exit requires a non-empty 'do' list"))
    else:
        for i, item in enumerate(do):
            _validate_action(item, f"{path}.do[{i}]", errs)
    leave = raw.get("leave_frames", 1)
    if not isinstance(leave, int) or leave < 1:
        errs.append(_err(f"{path}.leave_frames", f"leave_frames must be a positive int, got {leave!r}"))


def _validate_step(raw: Any, path: str, errs: list[VerificationError]) -> None:
    if not isinstance(raw, dict) or len(raw) != 1:
        errs.append(_err(path, f"step must be a single-key mapping, got {raw!r}"))
        return
    (kind, val) = next(iter(raw.items()))
    if kind == "check":
        _validate_condition(val, f"{path}.check", errs)
    elif kind == "on_exit":
        _validate_on_exit(val, f"{path}.on_exit", errs)
    else:
        errs.append(
            _err(f"{path}.{kind}", f"unknown step type {kind!r} (allowed: {sorted(_VALID_STEP_KINDS)})")
        )


def _validate(raw: Any, filename: str | None, errs: list[VerificationError]) -> None:
    if not isinstance(raw, dict):
        errs.append(_err("rule", f"rule must be a YAML mapping, got {raw!r}"))
        return

    for key in sorted(set(raw) - _VALID_TOP_KEYS):
        errs.append(
            _err(key, f"unknown top-level key {key!r} (valid: {sorted(_VALID_TOP_KEYS)})")
        )

    schema = raw.get("schema_version")
    if schema != 2:
        errs.append(_err("schema_version", f"unsupported schema_version {schema!r} (expected 2)"))

    rule_id = raw.get("rule_id")
    if not isinstance(rule_id, str) or not rule_id:
        errs.append(_err("rule_id", f"rule requires a non-empty rule_id, got {rule_id!r}"))
    elif filename is not None:
        stem = Path(filename).stem
        if rule_id != stem:
            errs.append(
                _err(
                    "rule_id",
                    f"rule_id {rule_id!r} does not match filename stem {stem!r}",
                )
            )

    if "description" in raw and not isinstance(raw["description"], str):
        errs.append(_err("description", "description must be a string"))

    track = raw.get("track")
    if not isinstance(track, dict) or set(track) != {"class"}:
        errs.append(_err("track", f"track must be {{class: ...}}, got {track!r}"))
    else:
        class_name = track["class"]
        if not isinstance(class_name, str) or not class_name:
            errs.append(_err("track.class", "track.class must be a non-empty class name"))

    steps = raw.get("steps")
    if not isinstance(steps, list) or not steps:
        errs.append(_err("steps", "rule requires a non-empty 'steps' list"))
    else:
        for i, step in enumerate(steps):
            _validate_step(step, f"steps[{i}]", errs)


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


@dataclass
class Stage0Result:
    """Outcome of structural validation."""

    errors: list[VerificationError]
    #: The parsed :class:`Rule` AST, or None when validation failed.
    rule: Rule | None


def run_stage0(rule_yaml: str, filename: str | None = None) -> Stage0Result:
    """Validate raw rule YAML; returns pathed errors and (on success) the AST."""
    try:
        raw: Any = yaml.safe_load(rule_yaml)
    except yaml.YAMLError as exc:
        return Stage0Result(
            errors=[_err("rule", f"rule is not valid YAML: {exc}")],
            rule=None,
        )

    errs: list[VerificationError] = []
    if raw is None:
        errs.append(_err("rule", "empty rule document (YAML parsed to null)"))
    else:
        _validate(raw, filename, errs)
    if errs:
        return Stage0Result(errors=errs, rule=None)

    # Reuse the engine's authoritative parser to build the AST.
    try:
        rule = parse_rule(raw)
    except RuleSyntaxError as exc:
        # Defensive: the walker mirrors the grammar, so this should be unreachable.
        return Stage0Result(
            errors=[_err("rule", f"{exc}. Parser raised while validator passed; this is a bug.")],
            rule=None,
        )
    return Stage0Result(errors=[], rule=rule)


__all__ = ["Stage0Result", "run_stage0"]