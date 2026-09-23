"""Stage 2 -- formal / contradiction checks using z3-solver.

All check steps of a rule must eventually be armed *simultaneously* before
``on_exit`` can fire, so the conjunction of every check condition must be
satisfiable over the op value domains.

Encoding rules
--------------
* Each distinct **op-call signature** -- the op name plus its fully resolved
  argument structure (literals, ``$entity``, ``{frames: N}`` windows, and
  nested op calls) -- maps to *one* symbolic value.  Reusing the same op call
  in two places therefore reuses the same symbol, which is what makes
  "``>= X`` and ``< Y`` with ``Y < X``" detectable.
* Geometry-valued expressions (``bbox`` / ``centroid`` / ``relative_vector``),
  ``TimeExpr`` values, and variable references other than ``$entity`` can
  never be ordered by a numeric comparison at runtime (the engine returns
  ``ok=False``), so they are encoded as the constant ``False``.
* Symbolic domains come from the semantics of each op (``confidence``/``avg``
  in ``[0, 1]``, ``persisted_for`` in ``[0, window]``, areas/speeds
  non-negative, ...).  Bounds are conservative supersets of the real domains
  so a detected contradiction is always genuine.

Detection
---------
1. A single comparison can be unsatisfiable on its own (threshold outside the
   op's domain, e.g. ``persisted_for(frames 5) >= 6``).
2. Two different comparisons referencing the **same op-signature** can be
   pairwise contradictory (``area > 40000`` and ``area < 40000``).
3. Finally the *full* boolean formula (``all`` / ``any`` / ``not`` nesting
   included) is checked; a contradiction through the boolean structure alone
   (e.g. ``any(...)`` + ``not (...)``) is reported generically.

This is a full z3 encoding of the check conditions rather than a reduced
literal scan -- the rule files are small, so solver time is negligible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import z3

from sih.rules.types import (
    Check,
    Comparison,
    Condition,
    Literal,
    Logic,
    Not,
    OpCall,
    Rule,
    TimeExpr,
    VarRef,
)
from sih.verification._errors import (
    STAGE_FORMAL,
    VerificationError,
)

_FRAMEBUFFER_CAP = 60  # sih.input.framebuffer.DEFAULT_MAX_FRAMES


def _err(path: str, message: str) -> VerificationError:
    return VerificationError(stage=STAGE_FORMAL, message=message, path=path)


# ---------------------------------------------------------------------------
# Op-call signature identity
# ---------------------------------------------------------------------------


def _canon(expr: Any) -> tuple:
    """Stable canonical form of a ValueExpr (AST or raw)."""
    if isinstance(expr, Literal):
        return ("lit", type(expr.value).__name__, repr(expr.value))
    if isinstance(expr, VarRef):
        return ("var", expr.name)
    if isinstance(expr, TimeExpr):
        return ("time", expr.ref.kind, expr.ref.n)
    if isinstance(expr, OpCall):
        args = sorted((k, _canon(v)) for k, v in expr.args.items())
        return ("op", expr.op, tuple(args))
    return ("literal", repr(expr))


def _signature(op: str, args: dict[str, Any]) -> tuple:
    return (op, tuple(sorted((k, _canon(v)) for k, v in args.items())))


# ---------------------------------------------------------------------------
# Op value domains
# ---------------------------------------------------------------------------


def _window_n(args: dict[str, Any]) -> int | None:
    """Upper bound on a window op's result from its ``window`` argument."""
    window = args.get("window")
    if isinstance(window, TimeExpr):
        if window.ref.kind == "frames":
            return window.ref.n
        return 1  # offset targets a single frame entry
    if window is None:
        return _FRAMEBUFFER_CAP
    return None  # unpinned (extremely unlikely; treat as unbounded)


def _op_domain(op: str, args: dict[str, Any]) -> tuple[str, float | None, float | None]:
    """Return (kind, lower, upper) for an op call's symbolic value."""
    if op in {"is_visible", "occurred_in_window"}:
        return ("bool", None, None)
    if op in {"confidence"}:
        return ("real", 0.0, 1.0)
    if op == "persisted_for":
        return ("int", 0, float(_window_n(args) or 0))
    if op == "count_in_window":
        return ("int", 0.0, None)
    if op == "avg_in_window":
        metric = args.get("metric")
        upper = 1.0 if isinstance(metric, Literal) and metric.value == "confidence" else None
        return ("real", 0.0, upper)
    if op in {"speed", "area", "magnitude", "distance"}:
        return ("real", 0.0, None)
    if op == "angle":
        return ("real", 0.0, 360.0)
    return ("geometry", None, None)  # bbox / centroid / relative_vector


class _Domains:
    """Per-signature z3 symbols plus their bound assertions."""

    def __init__(self) -> None:
        self._symbols: dict[tuple, z3.ExprRef] = {}
        self._bounds: list[z3.BoolRef] = []

    def symbol_for(self, op: str, args: dict[str, Any]) -> z3.ExprRef:
        key = _signature(op, args)
        symbol = self._symbols.get(key)
        if symbol is not None:
            return symbol
        kind, lower, upper = _op_domain(op, args)
        name = f"{op}<{len(self._symbols)}>"
        if kind == "bool":
            symbol = z3.Bool(name)
        elif kind == "int":
            symbol = z3.Int(name)
        else:  # real / geometry fallback -> real
            symbol = z3.Real(name)
        self._symbols[key] = symbol
        if kind == "real":
            self._bounds.append(symbol >= z3.RealVal(lower))
        elif kind == "int":
            self._bounds.append(symbol >= 0)
        if upper is not None and kind in {"real", "int"}:
            upper_expr = z3.RealVal(upper) if kind == "real" else z3.IntVal(int(upper))
            self._bounds.append(symbol <= upper_expr)
        return symbol

    @property
    def domain_assertions(self) -> list[z3.BoolRef]:
        return list(self._bounds)


# ---------------------------------------------------------------------------
# Formula encoding
# ---------------------------------------------------------------------------


def _encode_value(expr: Any, model: _Domains, entity_sym: z3.ExprRef) -> tuple[str, Any]:
    """Return (kind, z3 expr-or-python) for one value expression."""
    if isinstance(expr, Literal):
        value = expr.value
        if isinstance(value, bool):
            return ("bool", z3.BoolVal(value))
        if isinstance(value, int):
            return ("int", z3.IntVal(value))
        if isinstance(value, float):
            return ("real", z3.RealVal(value))
        return ("str", value)
    if isinstance(expr, VarRef):
        if expr.name == "entity":
            return ("int", entity_sym)
        return ("other", None)
    if isinstance(expr, TimeExpr):
        return ("other", None)
    if isinstance(expr, OpCall):
        kind, _, _ = _op_domain(expr.op, expr.args)
        if kind == "geometry":
            return ("geometry", None)
        return (kind, model.symbol_for(expr.op, expr.args))
    return ("other", None)


def _comparison_formula(comp: Comparison, model: _Domains, entity_sym: z3.ExprRef) -> z3.BoolRef:
    left_kind, left = _encode_value(comp.left, model, entity_sym)
    right_kind, right = _encode_value(comp.right, model, entity_sym)

    if left_kind in {"other", "geometry"} or right_kind in {"other", "geometry"}:
        return z3.BoolVal(False)

    if left_kind == "str" or right_kind == "str":
        if left_kind == "str" and right_kind == "str":
            ops = {
                "gt": lambda a, b: a > b,
                "gte": lambda a, b: a >= b,
                "lt": lambda a, b: a < b,
                "lte": lambda a, b: a <= b,
                "eq": lambda a, b: a == b,
                "ne": lambda a, b: a != b,
            }
            return z3.BoolVal(bool(ops[comp.operator](left, right)))
        return z3.BoolVal(False)

    def to_arith(kind: str, value: Any) -> tuple[str, z3.ExprRef] | None:
        if kind == "bool":
            return ("int", z3.If(value, z3.IntVal(1), z3.IntVal(0)))
        if kind == "int":
            return ("int", value)
        if kind == "real":
            return ("real", value)
        return None

    left_a = to_arith(left_kind, left)
    right_a = to_arith(right_kind, right)
    if left_a is None or right_a is None:
        return z3.BoolVal(False)

    lk, lv = left_a
    rk, rv = right_a
    try:
        if lk == "real" or rk == "real":
            if lk == "int":
                lv = z3.ToReal(lv)
            if rk == "int":
                rv = z3.ToReal(rv)
        operator = comp.operator
        if operator == "gt":
            return lv > rv
        if operator == "gte":
            return lv >= rv
        if operator == "lt":
            return lv < rv
        if operator == "lte":
            return lv <= rv
        if operator == "eq":
            return lv == rv
        if operator == "ne":
            return lv != rv
    except z3.Z3Exception:
        return z3.BoolVal(False)
    return z3.BoolVal(False)


def _condition_formula(node: Condition, model: _Domains, entity_sym: z3.ExprRef) -> z3.BoolRef:
    if isinstance(node, Comparison):
        return _comparison_formula(node, model, entity_sym)
    if isinstance(node, Logic):
        children = [_condition_formula(c, model, entity_sym) for c in node.children]
        if node.op == "all":
            return z3.And(*children)
        return z3.Or(*children)
    if isinstance(node, Not):
        return z3.Not(_condition_formula(node.child, model, entity_sym))
    raise TypeError(f"unknown condition node {type(node).__name__}")


# ---------------------------------------------------------------------------
# Leaf collection (for precise pairwise reporting)
# ---------------------------------------------------------------------------


@dataclass
class _Leaf:
    path: str
    comparison: Comparison
    formula: z3.BoolRef
    symbols: list[z3.ExprRef] = field(default_factory=list)


def _collect_leaves(node: Condition, path: str, model: _Domains, entity_sym: z3.ExprRef, out: list[_Leaf]) -> None:
    if isinstance(node, Comparison):
        formula = _comparison_formula(node, model, entity_sym)
        symbols = []

        def grab(expr: Any) -> None:
            if isinstance(expr, OpCall):
                kind, _, _ = _op_domain(expr.op, expr.args)
                if kind != "geometry":
                    symbols.append(model.symbol_for(expr.op, expr.args))

        grab(node.left)
        grab(node.right)
        out.append(_Leaf(path=path, comparison=node, formula=formula, symbols=symbols))
        return
    if isinstance(node, Logic):
        op = node.op
        for i, child in enumerate(node.children):
            _collect_leaves(child, f"{path}.{op}[{i}]", model, entity_sym, out)
        return
    if isinstance(node, Not):
        _collect_leaves(node.child, f"{path}.not", model, entity_sym, out)


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def _sat_with(model: _Domains, goal: z3.BoolRef) -> bool:
    """True when ``goal`` is satisfiable together with the op-domain axioms."""
    solver = z3.Solver()
    solver.set("timeout", 10_000)
    solver.add(*model.domain_assertions)
    solver.add(goal)
    result = solver.check()
    return result == z3.sat  # unknown is treated as "no contradiction proven"


def run_stage2(rule: Rule) -> list[VerificationError]:
    """Detect unsatisfiable check-condition conjunctions in a parsed rule."""
    checks = [(i, step.condition) for i, step in enumerate(rule.steps) if isinstance(step, Check)]
    if not checks:
        return []

    model = _Domains()
    entity_sym = z3.Int("$entity")
    leaves: list[_Leaf] = []
    for i, condition in checks:
        _collect_leaves(condition, f"steps[{i}].check", model, entity_sym, leaves)

    if not leaves:
        # All comparisons are geometry/unresolvable -> every check is a hard
        # False at runtime, so no rule with a top-level 'all' structure could
        # ever arm.  Only report when there is at least one check.
        if checks:
            step_paths = [f"steps[{i}].check" for i, _ in checks]
            return [
                _err(
                    step_paths[0],
                    "every comparison in this rule resolves to an unencodable "
                    "(non-numeric) expression; such checks can never arm at runtime",
                )
            ]
        return []

    # --- 1. singletons: threshold outside the op's own value domain ---
    errors: list[VerificationError] = []
    for leaf in leaves:
        if not _sat_with(model, leaf.formula):
            errors.append(
                _err(
                    leaf.path,
                    _singleton_message(leaf.comparison),
                )
            )

    # --- 2. pairwise: two comparisons over the same op-signature ---
    if not errors:
        for i, a in enumerate(leaves):
            for b in leaves[i + 1 :]:
                shared = set(a.symbols) & set(b.symbols)
                if not shared:
                    continue
                if not _sat_with(model, z3.And(a.formula, b.formula)):
                    signs = ", ".join(sorted(s.__str__() for s in shared))
                    errors.append(
                        _err(
                            a.path,
                            f"contradiction with {b.path}: both constrain the same "
                            f"op value(s) ({signs}) in ways that can never hold "
                            f"simultaneously ({_describe(a.comparison)} vs "
                            f"{_describe(b.comparison)})",
                        )
                    )
                    break

    # --- 3. full boolean structure of all checks ---
    if not errors:
        encoded = [_condition_formula(cond, model, entity_sym) for _, cond in checks]
        goal = z3.And(*encoded)
        if not _sat_with(model, goal):
            step_indexes = [i for i, _ in checks]
            errors.append(
                _err(
                    f"steps[{step_indexes[0]}].check",
                    "the conjunction of all check conditions is unsatisfiable "
                    f"(checks {step_indexes} can never all be armed together)",
                )
            )

    return errors


def _describe(comp: Comparison) -> str:
    left = _describe_operand(comp.left)
    right = _describe_operand(comp.right)
    return f"{left} {comp.operator} {right}"


def _describe_operand(expr: Any) -> str:
    if isinstance(expr, Literal):
        return repr(expr.value)
    if isinstance(expr, VarRef):
        return f"${expr.name}"
    if isinstance(expr, TimeExpr):
        return repr(expr.ref)
    if isinstance(expr, OpCall):
        args = ", ".join(f"{k}={_describe_operand(v)}" for k, v in sorted(expr.args.items()))
        return f"{expr.op}({args})"
    return str(expr)


def _singleton_message(comp: Comparison) -> str:
    return (
        f"comparison {_describe(comp)} can never be satisfied: the threshold "
        f"({_describe_operand(comp.right)}) lies outside the op value's domain "
        f"({_describe_operand(comp.left)})"
    )


__all__ = ["run_stage2"]