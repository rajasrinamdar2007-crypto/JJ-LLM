"""Stage 1 -- semantic / near-duplicate detection (informational, never gates).

Two rules of the same ``track.class`` count as near-duplicates when the
*abstract* op-call signatures appearing in their **check** conditions overlap.
An abstract signature is the op name plus its argument structure with every
numeric literal value collapsed to a placeholder: ``metric``/window values and
``$entity`` keep their identity, but threshold numbers do not.  So two rules
that arm on the same ops (``persisted_for`` + ``avg_in_window(confidence)``)
and differ *only* in the right-hand threshold collide, while rules that use
different ops do not.

By design this stage:

* runs **after** Stage 0, so only rules that already parsed to a valid
  :class:`Rule` AST are ever compared (a malformed candidate has no
  signatures and is skipped, avoiding noise from garbage);
* compares **same-class** rules only (different classes are different
  intents);
* produces :class:`SimilarityFinding` records that surface to a human review
  gate.  It never sets ``passed=False``: near-duplicates are flagged, not
  auto-rejected, and never affect exit codes tracked by ``run_checks.py`` /
  ``ship_rules.py``.
"""

from __future__ import annotations

from sih.rules.types import (
    Check,
    Comparison,
    Literal,
    Logic,
    Not,
    OpCall,
    Rule,
    TimeExpr,
    VarRef,
)
from sih.verification._errors import SimilarityFinding

#: Minimum Jaccard overlap over abstract check signatures to flag a pair.
DEFAULT_SIMILARITY_THRESHOLD = 0.5


# ---------------------------------------------------------------------------
# Abstract op-call signature
# ---------------------------------------------------------------------------


def _abstract_literal(value: object) -> str:
    """Collapse numeric literals to a placeholder; keep code-like literals."""
    if isinstance(value, (int, float, bool)):
        return "<num>"
    return repr(value)


def _abstract(expr: object) -> str:
    """Abstract canonical form of one value expression (for dedup only)."""
    if isinstance(expr, Literal):
        return ("lit", _abstract_literal(expr.value))
    if isinstance(expr, VarRef):
        return ("var", expr.name)
    if isinstance(expr, TimeExpr):
        return ("time", expr.ref.kind, expr.ref.n)
    if isinstance(expr, OpCall):
        return (
            "op",
            expr.op,
            tuple(sorted((name, _abstract(arg)) for name, arg in expr.args.items())),
        )
    return ("literal", repr(expr))


def _collect_signatures(expr: object, out: set[tuple]) -> None:
    """Gather ``OpCall`` signatures reachable from a value expression."""
    if isinstance(expr, OpCall):
        out.add(_abstract(expr))
        for arg in expr.args.values():
            _collect_signatures(arg, out)


def _collect_condition(cond: object, out: set[tuple]) -> None:
    """Gather all op-call signatures inside a check condition tree."""
    if isinstance(cond, Comparison):
        _collect_signatures(cond.left, out)
        _collect_signatures(cond.right, out)
    elif isinstance(cond, Logic):
        for child in cond.children:
            _collect_condition(child, out)
    elif isinstance(cond, Not):
        _collect_condition(cond.child, out)


def abstract_signatures(rule: Rule) -> frozenset[tuple]:
    """Abstract signatures of every op call in the rule's **check** steps.

    on_exit payloads/confidences are deliberately excluded: they are output
    products that repeat across rules and would inflate similarity.
    """
    out: set[tuple] = set()
    for step in rule.steps:
        if isinstance(step, Check):
            _collect_condition(step.condition, out)
    return frozenset(out)


def jaccard(a: frozenset[tuple], b: frozenset[tuple]) -> float:
    """Jaccard overlap between two signature sets (0.0 for empty sets)."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------------------
# Threshold deltas (what a human would edit to disambiguate)
# ---------------------------------------------------------------------------


def _threshold_deltas(
    rule_a: Rule, rule_b: Rule, shared: set[tuple]
) -> tuple[str, ...]:
    """Describe numeric literal differences between two rules.

    Compares, per *shared* abstract signature, the numeric threshold literals
    actually compared against that signature in each rule's check conditions
    (the sibling operand of each comparison over that op call).  Only reports
    differences -- identical thresholds yield no notes.
    """
    a_nums = _sig_literal_map(rule_a, shared)
    b_nums = _sig_literal_map(rule_b, shared)

    notes: list[str] = []
    for sig in sorted(shared, key=repr):
        if a_nums[sig] == b_nums[sig]:
            continue
        notes.append(
            f"{_sig_text(sig)}: threshold {_fmt_nums(a_nums[sig])} -> {_fmt_nums(b_nums[sig])}"
        )
    return tuple(notes)


def _sig_literal_map(rule: Rule, shared: set[tuple]) -> dict[tuple, list[float]]:
    """Map each shared sig to the numeric sibling literals found in this rule."""
    out: dict[tuple, list[float]] = {sig: [] for sig in shared}
    for step in rule.steps:
        if isinstance(step, Check):
            _walk_comparisons(step.condition, shared, out)
    return {sig: sorted(vals) for sig, vals in out.items()}


def _walk_comparisons(
    cond: object, shared: set[tuple], out: dict[tuple, list[float]]
) -> None:
    if isinstance(cond, Comparison):
        for op_expr, sibling in ((cond.left, cond.right), (cond.right, cond.left)):
            if not isinstance(op_expr, OpCall):
                continue
            sig = _abstract(op_expr)
            if sig in shared:
                out[sig].extend(_numeric_literals(sibling))
    elif isinstance(cond, Logic):
        for child in cond.children:
            _walk_comparisons(child, shared, out)
    elif isinstance(cond, Not):
        _walk_comparisons(cond.child, shared, out)


def _numeric_literals(expr: object) -> list[float]:
    """Numeric literal values reachable from an operand expression."""
    if isinstance(expr, Literal) and isinstance(expr.value, (int, float)):
        return [float(expr.value)]
    if isinstance(expr, OpCall):
        out: list[float] = []
        for arg in expr.args.values():
            out.extend(_numeric_literals(arg))
        return out
    return []


def _fmt_nums(nums: list[float]) -> str:
    return ", ".join(repr(n) for n in nums) or "<none>"


def _sig_text(sig: tuple) -> str:
    """Human-readable rendering of an abstract signature tuple."""
    op = sig[1]
    args = ", ".join(f"{name}={_text(v)}" for name, v in sig[2])
    return f"{op}({args})"


def _text(node: tuple) -> str:
    kind = node[0]
    if kind == "lit":
        return node[1] if node[1] == "<num>" else _fmt_lit(node[1])
    if kind == "var":
        return f"${node[1]}"
    if kind == "time":
        return f"{{{node[1]}: {node[2]}}}"
    if kind == "op":
        args = ", ".join(f"{name}={_text(v)}" for name, v in node[2])
        return f"{node[1]}({args})"
    return repr(node)


def _fmt_lit(value: object) -> str:
    if isinstance(value, str):
        return value
    return repr(value)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def _compare(
    candidate: Rule,
    existing: Rule,
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> SimilarityFinding | None:
    """One SimilarityFinding if ``candidate`` vs ``existing`` crosses threshold."""
    cand_sigs = abstract_signatures(candidate)
    exist_sigs = abstract_signatures(existing)
    score = jaccard(cand_sigs, exist_sigs)
    if score < threshold:
        return None
    shared = cand_sigs & exist_sigs
    return SimilarityFinding(
        candidate=candidate.rule_id,
        existing=existing.rule_id,
        class_name=candidate.class_name,
        similarity=round(score, 4),
        shared_signatures=tuple(sorted((_sig_text(s) for s in shared), key=str)),
        differing_thresholds=_threshold_deltas(candidate, existing, shared),
    )


def find_for_candidate(
    candidate: Rule,
    existing: list[Rule],
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[SimilarityFinding]:
    """Near-duplicate findings for one new candidate against a corpus.

    Used by the TUI after a generated rule passes validation: compares the
    just-built candidate against the rest of ``LM-rules/`` and returns whatever
    should be surfaced to a human reviewer.
    """
    findings: list[SimilarityFinding] = []
    for other in existing:
        if other.rule_id == candidate.rule_id:
            continue
        if other.class_name != candidate.class_name:
            continue
        if not abstract_signatures(other):
            continue
        finding = _compare(candidate, other, threshold=threshold)
        if finding is not None:
            findings.append(finding)
    return findings


def find_in_set(
    rules: list[Rule],
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[SimilarityFinding]:
    """Near-duplicate findings across a whole rule set (pairwise, same class).

    Used by ``run_checks.py`` / ``ship_rules.py`` over the verified set.  The
    ``candidate``/``existing`` labels are just the two ids of the pair.
    """
    findings: list[SimilarityFinding] = []
    for i, rule_a in enumerate(rules):
        for rule_b in rules[i + 1 :]:
            if rule_a.class_name != rule_b.class_name:
                continue
            if not abstract_signatures(rule_a) or not abstract_signatures(rule_b):
                continue
            finding = _compare(rule_a, rule_b, threshold=threshold)
            if finding is not None:
                findings.append(finding)
    return findings


__all__ = [
    "DEFAULT_SIMILARITY_THRESHOLD",
    "abstract_signatures",
    "find_for_candidate",
    "find_in_set",
    "jaccard",
]
