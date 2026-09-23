"""Rule verification pipeline.

Sits between rule generation (the LLM integration, built separately) and rule
deployment: it takes a raw schema-v2 rule YAML *string* and runs three
checks in order, fail-fast:

* :mod:`sih.verification._stage0` -- structural validation (pathed errors,
  including op argument name/arity/type checks against the real ``OPS``
  signatures).
* :mod:`sih.verification._stage2` -- formal / contradiction checks with
  z3-solver (are all check conditions simultaneously satisfiable?).
* :mod:`sih.verification._stage4` -- bounded property-based behaviour checks
  over synthetic entity histories (no-exception, monotonic arming, exact
  ``leave_frames`` firing boundary) plus the two real rules' threshold
  boundaries and mutation diagnostics.

There is deliberately no LLM in this loop: a teammate's integration will call
:func:`run_verification_pipeline` with generated YAML.  ``errors`` are
structured so a retry prompt can be built directly from ``message`` + ``path``.
"""

from __future__ import annotations

from sih.verification._errors import (
    STAGE_DEDUP,
    STAGE_FORMAL,
    STAGE_PROPERTY,
    STAGE_STRUCTURAL,
    SimilarityFinding,
    StageOutcome,
    VerificationError,
    VerificationResult,
)
from sih.verification._stage0 import Stage0Result, run_stage0
from sih.verification._stage1_dedup import find_for_candidate, find_in_set
from sih.verification._stage2 import run_stage2
from sih.verification._stage4 import run_stage4


def run_verification_pipeline(
    rule_yaml: str,
    *,
    filename: str | None = None,
    stage4_seed: int = 0,
    stage4_samples: int = 150,
) -> VerificationResult:
    """Single entry point for rule verification.

    Runs Stage 0 (structural) -> Stage 2 (formal) -> Stage 4 (property) in
    order, fail-fast.  Accepts raw rule YAML as a string so the caller's LLM
    integration can pass generated text directly.

    ``filename`` is optional and triggers only the ``rule_id``-must-match-
    filename-stem check of Stage 0.
    """
    stage0: Stage0Result = run_stage0(rule_yaml, filename=filename)
    if stage0.errors:
        return VerificationResult(
            passed=False,
            stage_reached=STAGE_STRUCTURAL,
            errors=stage0.errors,
        )

    rule = stage0.rule
    assert rule is not None  # stage0 returned no errors

    stage2_errors = run_stage2(rule)
    if stage2_errors:
        return VerificationResult(
            passed=False,
            stage_reached=STAGE_FORMAL,
            errors=stage2_errors,
        )

    stage4_errors = run_stage4(
        rule_yaml, rule, seed=stage4_seed, samples=stage4_samples
    )
    if stage4_errors:
        return VerificationResult(
            passed=False,
            stage_reached=STAGE_PROPERTY,
            errors=stage4_errors,
        )

    return VerificationResult(passed=True, stage_reached="all", errors=[])


__all__ = [
    "STAGE_DEDUP",
    "STAGE_FORMAL",
    "STAGE_PROPERTY",
    "STAGE_STRUCTURAL",
    "SimilarityFinding",
    "Stage0Result",
    "StageOutcome",
    "VerificationError",
    "VerificationResult",
    "find_for_candidate",
    "find_in_set",
    "run_stage0",
    "run_stage2",
    "run_stage4",
    "run_verification_pipeline",
]
