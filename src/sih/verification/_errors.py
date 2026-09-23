"""Shared result types for the rule-verification pipeline.

:class:`VerificationError` fields are deliberately machine-actionable: a
teammate's LLM integration will feed ``message`` + ``path`` back into a retry
prompt, so errors name the offending construct, the location (a structural
YAML path such as ``steps[0].check.gte.left.op``), and — where relevant —
the set of valid alternatives.
"""

from __future__ import annotations

from dataclasses import dataclass, field

STAGE_STRUCTURAL = "stage0"
STAGE_FORMAL = "stage2"
STAGE_PROPERTY = "stage4"
#: Informational near-duplicate detection.  Findings never set ``passed=False``.
STAGE_DEDUP = "stage1"


@dataclass(frozen=True)
class VerificationError:
    """One machine-actionable verification failure."""

    #: The pipeline stage that found it: "stage0" | "stage2" | "stage4".
    stage: str
    #: Specific, self-contained description of what is wrong and (when
    #: relevant) what the valid options were.
    message: str
    #: Structural path into the rule YAML, e.g. "steps[0].check.gte.left.op".
    #: Empty when the failure is not attributable to a single YAML node
    #: (e.g. a runtime behavioural invariant violation).
    path: str

    def __str__(self) -> str:
        return f"[{self.stage}] {self.path or '<rule>'}: {self.message}"


@dataclass(frozen=True)
class StageOutcome:
    """Per-stage result so a caller can report a status *per stage* rather
    than only a combined verdict (e.g. ``run_checks.py`` prints one line for
    Stage 0, Stage 2 and Stage 4).

    Every pipeline stage that could have run is represented, even when
    fail-fast stopped earlier -- the unreached stages carry ``status ==
    "SKIPPED"`` with a note naming the stage that failed, so nothing is
    silently omitted.
    """

    #: The stage id: ``STAGE_STRUCTURAL`` | ``STAGE_FORMAL`` | ``STAGE_PROPERTY``.
    stage: str
    #: "PASS" | "FAIL" | "SKIPPED".
    status: str
    #: Errors produced by this stage (empty unless ``status == "FAIL"``).
    errors: list[VerificationError] = field(default_factory=list)
    #: Human note, e.g. why a stage was skipped.  Empty for PASS/FAIL.
    note: str = ""


@dataclass(frozen=True)
class SimilarityFinding:
    """One informational near-duplicate report between two same-class rules.

    Produced by Stage 1 (semantic / near-duplicate detection).  It is a *flag
    for a human review gate*, never a hard failure: it does not set
    ``passed=False`` and must never affect any exit-code contract.
    """

    #: The candidate rule (for set-wide pairs, the lexically-equal-to-first id).
    candidate: str
    #: The existing rule the candidate resembles.
    existing: str
    #: The shared ``track.class`` both rules arm on.
    class_name: str
    #: Jaccard overlap (0..1) over the rules' abstract check signatures.
    similarity: float
    #: The shared abstract op-call signatures the similarity is based on.
    shared_signatures: tuple[str, ...]
    #: Human-readable notes of where the two rules differ, e.g. only the
    #: numeric thresholds attached to a shared signature.
    differing_thresholds: tuple[str, ...] = ()

    def __str__(self) -> str:
        return (
            f"[{STAGE_DEDUP}] {self.candidate} may be a near-duplicate of "
            f"{self.existing} (class {self.class_name}, "
            f"similarity {self.similarity:.2f})"
        )


@dataclass
class VerificationResult:
    """Outcome of an entire ``run_verification_pipeline`` invocation."""

    passed: bool
    #: Farthest stage completed/reached: "stage0" | "stage2" | "stage4" |
    #: "all".  A stopped pipeline reports the failing stage.
    stage_reached: str
    errors: list[VerificationError] = field(default_factory=list)
    #: One :class:`StageOutcome` per pipeline stage, always including entries
    #: for stages that fail-fast skipped (those are ``"SKIPPED"``).
    stages: list[StageOutcome] = field(default_factory=list)
    #: Informational Stage 1 near-duplicate findings (never gates anything).
    similarities: list[SimilarityFinding] = field(default_factory=list)


__all__ = [
    "STAGE_DEDUP",
    "STAGE_FORMAL",
    "STAGE_PROPERTY",
    "STAGE_STRUCTURAL",
    "SimilarityFinding",
    "VerificationError",
    "VerificationResult",
]
