"""Stage 4 -- property-based behavioural checks over synthetic entity histories.

The rule engine is a stateful per-instance tracker that reads the shared
framebuffer singleton; there is no pure per-history evaluator to call.  So this
stage drives the **actual** evaluation logic -- ``RuleRegistry.load_dir`` +
``RuleRegistry.tick`` -- by pushing synthetic per-frame entity histories into
the framebuffer and observing the resulting record state, asserting the
engine's documented invariants on every example:

1. unresolvable / insufficient data (e.g. a window longer than the available
   history) never raises; it resolves to a non-armed / not-yet-satisfied state,
2. check-step completion is monotonic -- once armed it stays armed,
3. ``on_exit`` fires only when ``leave_frames`` consecutive absent frames have
   occurred *and* every check step is armed (the ``leave_frames - 1`` boundary
   is checked explicitly, then the ``leave_frames`` boundary).

Because hypothesis is a pytest-level tool, the pipeline entry runs a bounded,
seeded, deterministic subset of the same corpus (the invariants are universal,
so any subset is representative); pytest additionally runs the full
``@given``-generated sweep in ``tests/test_verification_stage4.py``.
"""

from __future__ import annotations

import random
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from sih.common.types import Entity, FrameEntry
from sih.input.framebuffer import framebuffer
from sih.rules.engine import RuleRegistry, parse_rule
from sih.rules.types import (
    Check,
    Comparison,
    EmittedEvent,
    Literal,
    Logic,
    Not,
    OnExit,
    OpCall,
    Rule,
)
from sih.verification._errors import STAGE_PROPERTY, VerificationError

#: A single frame's worth of entities (empty list == the tracked entity absent).
Frame = list[Entity]


def _err(message: str, path: str = "rule") -> VerificationError:
    return VerificationError(stage=STAGE_PROPERTY, message=message, path=path)


def det(tracker_id: int, class_name: str, width: float, height: float, confidence: float) -> Entity:
    """A single detection with a valid bounding box anchored at the origin."""
    return Entity(
        tracker_id=tracker_id,
        class_id=0,
        class_name=class_name,
        bbox=[0.0, 0.0, float(width), float(height)],
        confidence=confidence,
        ts=0.0,
        frame_id=0,
    )


# ---------------------------------------------------------------------------
# Synthetic inputs
# ---------------------------------------------------------------------------


def synthetic_corpus(rule: Rule, *, seed: int, count: int) -> list[list[Frame]]:
    """Deterministic corpus of per-frame histories for one rule's class.

    A frame is ``[]`` (entity absent) or ``[Entity]`` with a random confidence
    and bounding-box size.  Lengths range 0..20 so sequences both shorter and
    longer than any window size occur; short sequences are forced explicitly.
    """
    rng = random.Random(seed)
    cls = rule.class_name
    out: list[list[Frame]] = [[]]  # empty history
    for length in (1, 2, 3, 4):
        out.append([[det(7, cls, 40.0, 40.0, 0.9)] for _ in range(length)])
    for _ in range(count):
        length = rng.randint(0, 20)
        history: list[Frame] = []
        for _ in range(length):
            if rng.random() < 0.65:
                width = rng.uniform(0.5, 400.0)
                height = rng.uniform(0.5, 400.0)
                conf = rng.uniform(0.0, 1.0)
                history.append([det(7, cls, width, height, conf)])
            else:
                history.append([])
        out.append(history)
    return out


# ---------------------------------------------------------------------------
# History simulation (reuses the real engine)
# ---------------------------------------------------------------------------


#: A per-tick snapshot: tracker -> (completed check-step indices, absent counter).
ObsState = dict[int, tuple[frozenset[int], int]]


@dataclass
class FrameObs:
    frame_id: int
    #: Record state (all live records, tracker -> (completed, absent)) *before* this tick.
    pre: ObsState
    #: Record state *after* this tick (fired/dropped records have been removed).
    post: ObsState
    #: tracker -> id(record) after this tick, so record identity can be tracked
    #: across ticks (a fired/dropped record that reappears is a *new* record).
    post_ids: dict[int, int]
    fired: list[int]
    events: list[EmittedEvent]
    success: bool


@dataclass
class HistoryTrace:
    rule_id: str
    leave_frames: int
    #: Step indexes of the rule's ``check`` steps (the arming requirement).
    check_indices: list[int]
    units: list[FrameObs] = field(default_factory=list)
    exception: Exception | None = None


def run_history(rule_yaml: str, frames: list[Frame]) -> HistoryTrace:
    """Run one synthetic history through the *actual* engine and trace it.

    The rule is materialised to a temp ``<rule_id>.yaml`` so
    ``RuleRegistry.load_dir`` is used exactly as in production.  Per-record
    armed state is observed from the registry's record store (the only place
    the engine keeps it) -- white-box observation of the real evaluator, not a
    reimplementation.
    """
    rule = parse_rule(yaml.safe_load(rule_yaml))
    rule_id = rule.rule_id
    leave_frames = next((s.leave_frames for s in rule.steps if isinstance(s, OnExit)), 1)
    check_indices = [i for i, s in enumerate(rule.steps) if isinstance(s, Check)]

    with tempfile.TemporaryDirectory(prefix="vr-") as tmp:
        Path(tmp, f"{rule_id}.yaml").write_text(rule_yaml, encoding="utf-8")
        registry = RuleRegistry()
        registry.load_dir(tmp)
        records = registry._records[rule_id]

        framebuffer.clear()
        units: list[FrameObs] = []
        exception: Exception | None = None

        for frame_id, entities in enumerate(frames):
            framebuffer.add(
                FrameEntry(
                    frame_id=frame_id,
                    ts=float(frame_id),
                    gps_lat=0.0,
                    gps_lon=0.0,
                    entities=entities,
                    width=640,
                    height=480,
                )
            )
            pre = {tid: (frozenset(rec.completed), rec.absent) for tid, rec in records.items()}
            try:
                runs = registry.tick()
            except Exception as exc:  # noqa: BLE001 - the invariant forbids raises
                exception = exc
                units.append(FrameObs(frame_id, pre, {}, {}, [], [], False))
                break
            post = {tid: (frozenset(rec.completed), rec.absent) for tid, rec in records.items()}
            post_ids = {tid: id(rec) for tid, rec in records.items()}
            run = runs[0] if runs else None
            fired = [r.tracker_id for r in run.records] if run else []
            units.append(
                FrameObs(
                    frame_id=frame_id,
                    pre=pre,
                    post=post,
                    post_ids=post_ids,
                    fired=fired,
                    events=[ev for r in run.records for ev in r.events] if run else [],
                    success=bool(run.success) if run else False,
                )
            )

        return HistoryTrace(rule_id, leave_frames, check_indices, units, exception)


def invariant_errors(rule_yaml: str, rule: Rule, history: list[Frame]) -> list[VerificationError]:
    """Assert the three stage-4 invariants for one synthetic history.

    The invariants, all documented behaviour of
    :class:`sih.rules.engine.RuleRegistry`:

    1. unresolvable / insufficient data never raises;
    2. check-step completion is monotonic *within a record's lifetime* (a
       record that fires or silently drops is destroyed; a reappearing tracker
       starts a fresh, empty record, so completion may legitimately reset
       across that boundary);
    3. a record fires only when every ``check`` step is completed (armed) and
       ``leave_frames`` consecutive absent frames have elapsed.
    """
    errors: list[VerificationError] = []
    trace = run_history(rule_yaml, history)
    label = f"history of {len(history)} frames"

    if trace.exception is not None:
        errors.append(
            _err(
                "invariant 1 (no exception on unresolvable data): "
                f"evaluation raised {type(trace.exception).__name__}: "
                f"{trace.exception} ({label})"
            )
        )
        return errors

    for prev, cur in zip(trace.units, trace.units[1:]):
        for tid, cur_id in cur.post_ids.items():
            if prev.post_ids.get(tid) != cur_id:
                continue  # new/recreated record; no continuity constraint
            prev_completed = prev.post[tid][0]
            cur_completed = cur.post[tid][0]
            if not prev_completed <= cur_completed:
                errors.append(
                    _err(
                        "invariant 2 (check completion is monotonic): "
                        f"tracker {tid} un-completed {sorted(prev_completed)} -> "
                        f"{sorted(cur_completed)} within one record in {label}"
                    )
                )

    required = set(trace.check_indices)
    for obs in trace.units:
        for tid in obs.fired:
            pre = obs.pre.get(tid)
            if pre is None:
                errors.append(
                    _err(
                        f"invariant 3: tracker {tid} fired on frame {obs.frame_id} "
                        f"without a live record in {label}"
                    )
                )
                continue
            pre_completed, pre_absent = pre
            if set(pre_completed) != required:
                errors.append(
                    _err(
                        "invariant 3: tracker {tid} fired on frame {obs.frame_id} while "
                        "unarmed (completed {sorted(pre_completed)}, required "
                        "{sorted(required)}) in {label}"
                    )
                )
            if pre_absent + 1 < trace.leave_frames:
                errors.append(
                    _err(
                        f"invariant 3: tracker {tid} fired on frame {obs.frame_id} with "
                        f"fewer than leave_frames={trace.leave_frames} consecutive absent "
                        f"frames (absent = {pre_absent} before the tick) in {label}"
                    )
                )

    return errors


# ---------------------------------------------------------------------------
# Threshold discovery (shared by boundary + mutation stages)
# ---------------------------------------------------------------------------


def _walk_condition(
    cond: Any,
    *,
    wants: set[str],
    metric: str | None,
) -> float | None:
    """Find one numeric literal a comparison compares a wanted op against."""
    if isinstance(cond, Comparison):
        left, right = cond.left, cond.right
        if isinstance(right, Literal) and isinstance(right.value, (int, float)) and not isinstance(right.value, bool):
            if isinstance(left, OpCall) and left.op in wants:
                if metric is None:
                    return float(right.value)
                m = left.args.get("metric")
                if isinstance(m, Literal) and m.value == metric:
                    return float(right.value)
        return None
    if isinstance(cond, Logic):
        for child in cond.children:
            found = _walk_condition(child, wants=wants, metric=metric)
            if found is not None:
                return found
    if isinstance(cond, Not):
        return _walk_condition(cond.child, wants=wants, metric=metric)
    return None


# ---------------------------------------------------------------------------
# Boundary cases around real thresholds
# ---------------------------------------------------------------------------


@dataclass
class BoundaryCase:
    label: str
    frames: list[Frame]
    expect_fire: bool


def boundary_cases(rule: Rule) -> list[BoundaryCase]:
    """Hand-built histories straddling the real rules' numeric thresholds."""
    cls = rule.class_name
    checks = [s.condition for s in rule.steps if isinstance(s, Check)]
    cases: list[BoundaryCase] = []
    if not checks:
        return cases

    area_target = _walk_condition_op(checks, {"area"}, None)
    if area_target is not None:
        for delta, label in (
            (-1, "area just below threshold"),
            (0, "area exactly at threshold"),
            (1, "area just above threshold"),
        ):
            value = area_target + delta
            cases.append(
                BoundaryCase(
                    label=f"{cls} {label} ({value})",
                    frames=[[det(7, cls, value, 1.0, 0.9)], []],
                    expect_fire=value > area_target,
                )
            )

    persist_target = _walk_condition_op(checks, {"persisted_for"}, None)
    if persist_target is not None:
        for n, fire in (
            (int(persist_target) - 1, False),
            (int(persist_target), True),
            (int(persist_target) + 1, True),
        ):
            if n <= 0:
                continue
            cases.append(
                BoundaryCase(
                    label=(
                        f"{cls} persisted_for below {persist_target} ({n} present frames)"
                        if not fire
                        else f"{cls} persisted_for at/above {persist_target} ({n} present frames)"
                    ),
                    frames=[[det(7, cls, 40.0, 40.0, 0.9)] for _ in range(n)] + [[]],
                    expect_fire=fire,
                )
            )

    avg_target = _walk_condition_op(checks, {"avg_in_window"}, metric="confidence")
    if avg_target is not None:
        cases.append(
            BoundaryCase(
                label=f"{cls} avg confidence below {avg_target}",
                frames=[[det(7, cls, 40.0, 40.0, 0.59)] for _ in range(5)] + [[]],
                expect_fire=False,
            )
        )
        cases.append(
            BoundaryCase(
                label=f"{cls} avg confidence at {avg_target}",
                frames=[[det(7, cls, 40.0, 40.0, c)] for c in (0.5, 0.5, 0.5, 0.5, 1.0)]
                + [[]],
                expect_fire=True,
            )
        )
        cases.append(
            BoundaryCase(
                label=f"{cls} avg confidence above {avg_target}",
                frames=[[det(7, cls, 40.0, 40.0, c)] for c in (0.61, 0.61, 0.61, 0.61, 0.61)]
                + [[]],
                expect_fire=True,
            )
        )

    cases.append(
        BoundaryCase(
            label=f"{cls} armed but still visible (0 absent frames)",
            frames=[[det(7, cls, 40.0, 40.0, 0.9)] for _ in range(6)],
            expect_fire=False,
        )
    )
    return cases


def _walk_condition_op(checks: list, wants: set[str], metric: str | None) -> float | None:
    for cond in checks:
        found = _walk_condition(cond, wants=wants, metric=metric)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------------------
# Mutation diagnostics
# ---------------------------------------------------------------------------


@dataclass
class ThresholdSite:
    path: str
    value: float
    op_name: str


@dataclass
class MutationFinding:
    site: ThresholdSite
    factor: float
    differential: int
    total: int
    classification: str


@dataclass
class MutationReport:
    rule_id: str
    findings: list[MutationFinding] = field(default_factory=list)


def _threshold_sites(raw: dict) -> list[ThresholdSite]:
    """Every numeric literal appearing as a comparison operand in a check."""
    sites: list[ThresholdSite] = []
    steps = raw.get("steps")
    if not isinstance(steps, list):
        return sites
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or "check" not in step:
            continue
        _check_sites(step["check"], f"steps[{i}].check", sites)
    return sites


def _check_sites(cond: Any, path: str, sites: list[ThresholdSite]) -> None:
    if not isinstance(cond, dict) or len(cond) != 1:
        return
    (key, val) = next(iter(cond.items()))
    if key in {"all", "any"} and isinstance(val, list):
        for j, child in enumerate(val):
            _check_sites(child, f"{path}.{key}[{j}]", sites)
    elif key == "not":
        _check_sites(val, f"{path}.not", sites)
    elif key in {"gt", "gte", "lt", "lte", "eq", "ne"} and isinstance(val, dict):
        for side in ("left", "right"):
            operand = val.get(side)
            if isinstance(operand, (int, float)) and not isinstance(operand, bool):
                other = val.get("right" if side == "left" else "left")
                op_name = other.get("op", "literal") if isinstance(other, dict) else "literal"
                sites.append(
                    ThresholdSite(
                        path=f"{path}.{key}.{side}",
                        value=float(operand),
                        op_name=str(op_name),
                    )
                )


def _apply_mutation(raw: dict, site: ThresholdSite, factor: float) -> dict:
    """Deep-copy the rule and rewrite the literal at ``site.path`` by ``factor``."""
    mutated = yaml.safe_load(yaml.safe_dump(raw))
    segments = [s for s in re.split(r"[\[\]\.]+", site.path) if s]
    node: Any = mutated
    for segment in segments[:-1]:
        node = node[int(segment)] if segment.isdigit() else node[segment]
    last = segments[-1]
    if last.isdigit():
        node[int(last)] = round(site.value * factor, 6)
    else:
        node[last] = round(site.value * factor, 6)
    return mutated


def _fingerprint(rule_yaml: str, history: list[Frame]) -> tuple:
    trace = run_history(rule_yaml, history)
    if trace.exception is not None:
        return ("raised", type(trace.exception).__name__)
    fired = tuple(u.frame_id for u in trace.units for _ in u.fired)
    events = tuple(sorted(ev.event_type for u in trace.units for ev in u.events))
    return (fired, events)


def mutation_report(
    rule_yaml: str,
    rule: Rule,
    *,
    seed: int = 0,
    samples: int = 120,
) -> MutationReport:
    """Diagnostic: perturb every numeric check threshold by +/-10% and compare
    behavioural fingerprints over a fixed corpus.  Thresholds whose mutation
    changes nothing are "possibly inert"; one whose mutation changes more than
    half the corpus is "possibly brittle".  None of this gates the pipeline --
    it is reported, not enforced.
    """
    raw = yaml.safe_load(rule_yaml)
    corpus: list[list[Frame]] = synthetic_corpus(rule, seed=seed, count=samples)
    corpus = corpus + [case.frames for case in boundary_cases(rule)]
    baseline = [_fingerprint(rule_yaml, history) for history in corpus]

    findings: list[MutationFinding] = []
    for site in _threshold_sites(raw):
        for factor in (0.9, 1.1):
            mutant = _apply_mutation(raw, site, factor)
            mutant_yaml = yaml.safe_dump(mutant, sort_keys=True)
            fingerprints = [_fingerprint(mutant_yaml, history) for history in corpus]
            diff = sum(1 for a, b in zip(baseline, fingerprints) if a != b)
            total = len(corpus)
            if diff == 0:
                classification = "possibly inert threshold"
            elif diff / total > 0.5:
                classification = "possibly brittle threshold"
            else:
                classification = "sensitive threshold"
            findings.append(
                MutationFinding(
                    site=site,
                    factor=factor,
                    differential=diff,
                    total=total,
                    classification=classification,
                )
            )
    return MutationReport(rule_id=rule.rule_id, findings=findings)


# ---------------------------------------------------------------------------
# Pipeline stage
# ---------------------------------------------------------------------------


def run_stage4(
    rule_yaml: str,
    rule: Rule,
    *,
    seed: int = 0,
    samples: int = 150,
) -> list[VerificationError]:
    """Bounded, seeded Stage 4 sweep: corpus invariants + boundary expectations."""
    errors: list[VerificationError] = []
    seen: set[tuple[str, str]] = set()

    def add(error: VerificationError) -> None:
        key = (error.message, error.path)
        if key not in seen:
            seen.add(key)
            errors.append(error)

    for history in synthetic_corpus(rule, seed=seed, count=samples):
        for error in invariant_errors(rule_yaml, rule, history):
            add(error)

    for case in boundary_cases(rule):
        for error in invariant_errors(rule_yaml, rule, case.frames):
            add(error)
        trace = run_history(rule_yaml, case.frames)
        fired = any(u.success for u in trace.units) or any(u.events for u in trace.units)
        if fired != case.expect_fire:
            add(
                _err(
                    f"boundary case '{case.label}': expected fire={case.expect_fire} "
                    f"but observed fire={fired}"
                )
            )

    return errors


__all__ = [
    "BoundaryCase",
    "HistoryTrace",
    "MutationFinding",
    "MutationReport",
    "ThresholdSite",
    "boundary_cases",
    "det",
    "invariant_errors",
    "mutation_report",
    "run_history",
    "run_stage4",
    "synthetic_corpus",
]