"""Run the full verification pipeline over LM-rules/, then ship verified rules.

This is the deploy path for the rule authoring repo (JJ-LLM).  It is
**verification-gated**:

* Every *discovered* // YAML in ``LM-rules/`` is run through the full
  stage0/2/4 pipeline (structural, formal/z3-contradiction, property).
  Rules that fail any gate are **excluded from the shipment** and reported
  as such; the author-side ``LM-rules/`` folder keeps every rule (good and
  bad) exactly where it is, so failing rules stay visible and can never be
  force-shipped, allow-listed or removed.  Only rules passing all gates are
  candidates to deploy.
* A cross-file (deploy-gate) pass re-checks, entirely over z3 and same
  entity classes, that the verified rules are jointly satisfiable.
* The resolved ``numpy`` + ``z3-solver`` versions in author and deploy
  ``uv.lock`` files must match the pinned versions exactly.
* Files are copied atomically (tmp + rename) into the target ``LM-rules/``.
* ``MANIFEST.json`` is written *last*, after every rule file is in place,
  so it always matches disk.  Shipping is idempotent (identical files are
  not rewritten, and final output is byte-stable).
* An append-only ``SHIP_LOG.jsonl`` records every shipment.

Synthetic test fixtures (``contradictory_area.yaml``,
``unparseable_broken.yaml``) are reported but never gate nor ship.

Usage::

    uv run python ship_rules.py            # dry-run (default; safe)
    uv run python ship_rules.py --ship     # actually deploy
    uv run python ship_rules.py --quiet    # exit code only

Exit code: non-zero iff the shipment itself was blocked (cross-file
contradiction, version skew, or a ship refusal).  Rules that fail a
verification gate never block nor ship; they remain on the author side.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from sih.rules.engine import RuleSyntaxError, parse_rule
from sih.rules.types import Rule
from sih.verification import (
    STAGE_FORMAL,
    STAGE_PROPERTY,
    STAGE_STRUCTURAL,
    VerificationResult,
    find_in_set,
    run_verification_pipeline,
)
from sih.verification._ship import (
    MANIFEST_NAME,
    SHIP_LOG_NAME,
    ShipRefusedError,
    VerifiedRule,
    run_cross_file_contradictions,
    ship_rules,
)

ROOT = Path(__file__).parent

#: Source LM-rules directory (author side).
RULES_DIR = ROOT / "LM-rules"

#: Target LM-rules directory (deploy side: the detection project).
TARGET_DIR = ROOT.parent / "JJ-object-detection" / "LM-rules"

#: Lockfiles whose resolved versions must agree.
AUTHOR_LOCK = ROOT / "uv.lock"
DEPLOY_LOCK = ROOT.parent / "JJ-object-detection" / "uv.lock"

#: Append-only shipment log.
SHIP_LOG = ROOT / SHIP_LOG_NAME

SYNTHETIC_FIXTURES = ("contradictory_area.yaml", "unparseable_broken.yaml")


def _stage_num(stage: str) -> int:
    return int(stage[-1])


def _discover() -> list[Path]:
    return sorted(
        p for p in RULES_DIR.glob("*.yaml") if p.name not in SYNTHETIC_FIXTURES
    )


def _check_rules_discovery_has_fixture_files() -> None:
    for name in SYNTHETIC_FIXTURES:
        if not (RULES_DIR / name).exists():
            print(f"  WARN missing synthetic fixture {name} (report-only but expected)")


def _verify_one(
    rule_path: Path,
) -> tuple[bool, Rule | None, bytes | None, VerificationResult | None]:
    """Return (passed, parsed_rule_or_None, raw_rule_bytes_or_None, result_or_None)."""
    print(f"\n=== {rule_path.name} ===")
    try:
        raw_bytes = rule_path.read_bytes()
        rule_yaml = raw_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"  UNREADABLE {exc}; treated as failure")
        return False, None, None, None

    try:
        data = yaml.safe_load(rule_yaml)
        result = run_verification_pipeline(rule_yaml, filename=str(rule_path))
        rule = parse_rule(data) if result.passed else None
    except (yaml.YAMLError, yaml.parser.ParserError, yaml.scanner.ScannerError) as exc:
        print("  UNPARSEABLE (not valid YAML)")
        print(f"    [{exc.__class__.__name__}] {exc}")
        return False, None, None, None
    except RuleSyntaxError as exc:
        print("  UNPARSEABLE (not a loadable rule: structural parse failed)")
        print(f"    [{exc.__class__.__name__}] {exc}")
        return False, None, None, None
    except Exception as exc:  # noqa: BLE001 -- never let one bad file abort the run
        print("  UNPARSEABLE (unexpected load error)")
        print(f"    [{exc.__class__.__name__}] {exc}")
        return False, None, None, None

    stage_order = (STAGE_STRUCTURAL, STAGE_FORMAL, STAGE_PROPERTY)
    if result.passed:
        print("  PASS (all stages)")
        for stage in stage_order:
            print(f"    stage {_stage_num(stage)}: PASS")
        return True, rule, raw_bytes, result

    print("  FAIL (verification gate not reached)")
    for stage in stage_order:
        if stage == result.stage_reached:
            print(f"    stage {_stage_num(stage)}: FAIL")
            for error in result.errors:
                print(f"      [{error.path}] {error.message}")
        elif _stage_num(stage) < _stage_num(result.stage_reached):
            print(f"    stage {_stage_num(stage)}: PASS")
        else:
            print(f"    stage {_stage_num(stage)}: SKIPPED")
    return False, None, None, None


def _run_fixtures() -> None:
    print("\n=== Synthetic test fixtures (report-only; never gates or ships) ===")
    for name in SYNTHETIC_FIXTURES:
        _verify_one(RULES_DIR / name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify and ship LM-rules.")
    parser.add_argument(
        "--ship", action="store_true", help="actually deploy (default: dry-run)"
    )
    parser.add_argument("--quiet", action="store_true", help="suppress per-rule detail")
    args = parser.parse_args(argv)
    if args.quiet:
        import contextlib

        ctx = contextlib.redirect_stdout(sys.stderr)
        ctx.__enter__()
        try:
            return _main(should_ship=args.ship)
        finally:
            ctx.__exit__(None, None, None)
    return _main(should_ship=args.ship)


def _main(*, should_ship: bool) -> int:
    print("=== JJ-LLM rule deployment (fail-stop) ===")
    _check_rules_discovery_has_fixture_files()

    discovered = _discover()
    verified: list[VerifiedRule] = []
    verified_rules: list[tuple[str, str, Rule]] = []
    failed: list[str] = []
    for rule_path in discovered:
        passed, rule, raw_bytes, result = _verify_one(rule_path)
        if not passed:
            failed.append(rule_path.name)
            continue
        assert rule is not None and raw_bytes is not None and result is not None
        verified.append(
            VerifiedRule(
                rule_id=rule.rule_id,
                class_name=rule.class_name,
                filename=rule_path.name,
                source_bytes=raw_bytes,
                verified=result,
            )
        )
        verified_rules.append((rule_path.name, rule.rule_id, rule))

    _run_fixtures()

    print(
        f"\nLM-rules/: {len(discovered)} discovered, {len(verified)} verified, {len(failed)} failed"
    )
    if failed:
        print("\nEXCLUDED FROM SHIP (kept on author side, never deployed):")
        for name in failed:
            print(f"  - {name}")
        if not verified:
            print("NOTHING SHIPPED: no rule passed all verification gates.")
            return 1

    # Stage 1 (semantic near-duplicate detection) is informational and never
    # blocks or changes the shipment: near-duplicates are for a human reviewer.
    # It runs over the same verified set that is about to ship.
    print(
        "\n=== Stage 1 (semantic near-duplicate detection; informational, never gates) ==="
    )
    findings = find_in_set([rule for _, _, rule in verified_rules])
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

    cross_errors = run_cross_file_contradictions(verified_rules)
    print("\n=== Cross-file deploy gate (z3, shared entity classes) ===")
    if cross_errors:
        for error in cross_errors:
            print(f"  FAIL [{error.path}] {error.message}")
        print("DEPLOY BLOCKED: cross-file contradiction in verified rules.")
        return 1
    print("  PASS (no cross-file contradictions among deployed rules)")

    try:
        result = ship_rules(
            verified,
            target_dir=TARGET_DIR,
            author_lock=AUTHOR_LOCK,
            deploy_lock=DEPLOY_LOCK,
            ship_log=SHIP_LOG,
            expected_pins=None,
            dry_run=not should_ship,
        )
    except ShipRefusedError as exc:
        print(f"\nDEPLOY BLOCKED: {exc}")
        return 1

    action = "DRY-RUN" if result.dry_run else "SHIPPED"
    print(f"\n{action} into {result.target_dir}")
    print(f"  copied   : {result.copied or 'none'}")
    print(f"  unchanged: {result.unchanged or 'none'}")
    print(
        f"  pruned   : {result.pruned or 'none'} (stale/unverified rule files removed from target)"
    )
    print(f"  manifest : {result.manifest_path or (TARGET_DIR / MANIFEST_NAME)}")
    print(f"  pins     : {result.pins}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
