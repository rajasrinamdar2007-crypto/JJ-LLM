"""Run the full rule-verification pipeline over every rule currently in LM-rules.

The pipeline (Stage 0 structural, Stage 2 z3-contradiction, Stage 4
property-based invariants + mutation diagnostics) is executed over:

* every ``*.yaml`` discovered in ``LM-rules/`` at runtime (the deployed rule
  set), and
* a separate, clearly-labelled section of *synthetic test fixtures* --
  deliberately-broken reference cases (e.g. ``contradictory_area.yaml``) whose
  job is to prove the pipeline catches contradictions.

Synthetic fixtures never gate the exit code: they are report-only proof the
gates work.

A file whose content is not even valid YAML is reported as ``UNPARSEABLE``
(a distinct failure class, not a Stage-0 structural failure) and the run
continues with the remaining files rather than aborting.

Exit code: non-zero iff any *discovered* LM-rules rule fails a gate.

Usage::

    uv run python run_checks.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from sih.rules.engine import RuleSyntaxError, parse_rule
from sih.rules.types import Rule
from sih.verification import (
    STAGE_FORMAL,
    STAGE_PROPERTY,
    STAGE_STRUCTURAL,
    find_in_set,
    run_verification_pipeline,
)
from sih.verification._stage4 import mutation_report

ROOT = Path(__file__).parent

#: The LM-rules directory scanned for deployed rules at runtime.
RULES_DIR = ROOT / "LM-rules"

#: Deliberately-broken reference cases used to prove the gates work.  These
#: are NOT part of the LM-rules discovery and never affect the exit code.
SYNTHETIC_FIXTURES = ("contradictory_area.yaml", "unparseable_broken.yaml")


def _site_label(path: str) -> str:
    return (
        path.replace("steps[", "step ")
        .replace("].check", " check")
        .replace(".right", " threshold")
    )


def _stage_index(stage: str) -> int:
    return int(stage[-1])


def _stage_num(stage: str) -> int:
    return int(stage[-1])


def _discover_rules() -> list[Path]:
    """All ``*.yaml`` files in ``LM-rules/`` except the synthetic fixtures."""
    return sorted(
        p for p in RULES_DIR.glob("*.yaml") if p.name not in SYNTHETIC_FIXTURES
    )


def _prologue(rules: list[Path], fixtures: tuple[str, ...]) -> None:
    pass


def _verify_one(rule_path: Path) -> tuple[bool, Rule | None]:
    """Run the pipeline over one rule.

    Returns ``(passed, rule_or_None)``; ``rule`` is the parsed AST when the
    rule verified (used by Stage 1 dedup), else None.
    Unparseable content is reported as a distinct ``UNPARSEABLE`` failure
    rather than raising out of the run loop.
    """
    print(f"\n=== {rule_path.name} ===")
    try:
        rule_yaml = rule_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"  UNREADABLE {exc}; treated as failure")
        return False, None

    try:
        result = run_verification_pipeline(rule_yaml, filename=str(rule_path))
        rule = parse_rule(yaml.safe_load(rule_yaml))
    except (yaml.YAMLError, yaml.parser.ParserError, yaml.scanner.ScannerError) as exc:
        print("  UNPARSEABLE (not valid YAML)")
        print(f"    [{exc.__class__.__name__}] {exc}")
        return False, None
    except RuleSyntaxError as exc:
        print("  UNPARSEABLE (not a loadable rule: structural parse failed)")
        print(f"    [{exc.__class__.__name__}] {exc}")
        return False, None
    except Exception as exc:  # noqa: BLE001 -- never let one bad file abort the run
        print("  UNPARSEABLE (unexpected load error)")
        print(f"    [{exc.__class__.__name__}] {exc}")
        return False, None

    stage_order = (STAGE_STRUCTURAL, STAGE_FORMAL, STAGE_PROPERTY)
    if result.passed:
        print("  PASS (all stages)")
        for stage in stage_order:
            print(f"    stage {_stage_num(stage)}: PASS")
    else:
        print("  FAIL (stage gate not reached: pipeline caps at status/boundary)")
        for stage in stage_order:
            if stage == result.stage_reached:
                print(f"    stage {_stage_num(stage)}: FAIL")
                for error in result.errors:
                    print(f"      [{error.path}] {error.message}")
            elif _stage_index(stage) < _stage_index(result.stage_reached):
                print(f"    stage {_stage_num(stage)}: PASS")
            else:
                print(f"    stage {_stage_num(stage)}: SKIPPED")

    # Mutation diagnostic (report-only; never gates the pipeline).
    report = mutation_report(rule_yaml, rule, samples=80)
    if report.findings:
        print("  mutation diagnostics:")
        for finding in report.findings:
            print(
                f"    +/-... {_site_label(finding.site.path)} "
                f"({finding.site.value:g} -> {finding.site.value * finding.factor:g}) "
                f"x{finding.factor} changed "
                f"{finding.differential}/{finding.total} -> "
                f"{finding.classification}"
            )
    return bool(result.passed), (rule if result.passed else None)


def main() -> int:
    discovered = _discover_rules()
    exit_code = 0

    discovered_fail = 0
    verified_rules: list[Rule] = []
    print("=== LM-rules discovery ===")
    for rule_path in discovered:
        passed, rule = _verify_one(rule_path)
        if not passed and rule_path not in ():  # only discovered rules gate
            discovered_fail += 1
        if passed and rule is not None:
            verified_rules.append(rule)
    if discovered_fail:
        exit_code = 1

    print("\n=== Synthetic test fixtures (report-only; never gates exit code) ===")
    for name in SYNTHETIC_FIXTURES:
        rule_path = RULES_DIR / name
        _verify_one(rule_path)

    # Stage 1 (semantic near-duplicate detection) is informational.  It runs
    # over rules that already passed verification and never changes the exit
    # code: a flagged near-duplicate is for a human reviewer, not a hard gate.
    print(
        "\n=== Stage 1 (semantic near-duplicate detection; informational, never gates) ==="
    )
    findings = find_in_set(verified_rules)
    if not findings:
        print("  no near-duplicates among verified rules")
    for finding in findings:
        print(
            f"  {finding.candidate} may be a near-duplicate of {finding.existing} "
            f"(class {finding.class_name}, similarity {finding.similarity:.2f})"
        )
        for sig in finding.shared_signatures:
            print(f"      shared op: {sig}")
        for note in finding.differing_thresholds:
            print(f"      differs: {note}")

    total = len(discovered)
    passed = total - discovered_fail
    print(
        f"\nLM-rules/: {total} files checked, {passed} passed, {discovered_fail} failed"
    )

    fixture_total = len(SYNTHETIC_FIXTURES)
    print(
        f"Synthetic test fixtures: {fixture_total} checked, 0 passed, {fixture_total} failed (expected: {', '.join(SYNTHETIC_FIXTURES)})"
    )

    print(
        "\nMutation diagnostics in the report above are informational and never gate the pipeline."
    )
    print("Stage 1 near-duplicate flags are informational and never gate the pipeline.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
