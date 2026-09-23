"""Deploy-only rule shipping with fail-stop guarantees.

The LLM authoring repo (JJ-LLM) is the *only* writer of the deployed rule set
(JJ-object-detection/LM-rules).  This module enforces, in order:

1. **Only verified rules ship.**  The caller supplies one :class:`VerifiedRule`
   per parsed rule; any entry whose ``verified.passed`` is False is refused.
   There is deliberately no *skip* mode and no allow-list: an unverified rule
   blocks the whole shipment.

2. **Cross-repo version agreement.**  The resolved ``numpy`` and ``z3-solver``
   versions in both projects (read from their ``uv.lock`` files) must match the
   pins exactly and equal each other.  A skew refuses the shipment before any
   file is touched (no partial copy).

3. **Atomic per-file copy.**  Each rule is written to a hidden ``.tmp`` file
   inside the target directory and renamed into place once flushed, so a crash
   mid-ship can never leave a truncated rule.

4. **Manifest last.**  ``MANIFEST.json`` (per-file sha256 + rule metadata and
   the deployed package pins) is written only after every rule file is in
   place, so the manifest always describes what is actually on disk.

5. **Idempotent.**  A target file whose content already matches its sha256 is
   not rewritten; re-shipping an identical set updates nothing except (when
   needed) the manifest.

6. **Append-only log.**  Every shipment appends one JSON line to
   ``SHIP_LOG.jsonl``; the log is never rewritten or truncated.

Synthetic test fixtures never participate: they are verification proof cases,
not deployable rules, and shipping ignores them entirely.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import z3

from sih.rules.types import Check, Rule
from sih.verification._errors import VerificationError, VerificationResult
from sih.verification._stage2 import (
    _collect_leaves,
    _describe,
    _Domains,
    _sat_with,
)

#: Exact pins the deploy gate must observe.  Keep in sync with both
#: ``pyproject.toml`` files -- the gate compares resolved versions against these.
EXPECTED_PINS: dict[str, str] = {"numpy": "2.5.3", "z3-solver": "5.1.0.0"}

#: Stage tag used on cross-file deploy errors (kept distinct from stage0/2/4).
STAGE_DEPLOY = "deploy"

MANIFEST_NAME = "MANIFEST.json"
SHIP_LOG_NAME = "SHIP_LOG.jsonl"


class ShipRefusedError(RuntimeError):
    """Raised when a shipment must not proceed (fail-stop precondition)."""


# ---------------------------------------------------------------------------
# Resolved-version gate (Problem 8: cross-repo skew)
# ---------------------------------------------------------------------------


def resolved_pins(uv_lock: Path) -> dict[str, str]:
    """Extract the resolved ``numpy``/``z3-solver`` versions from a uv.lock."""
    resolved: dict[str, str] = {}
    name: str | None = None
    for raw in uv_lock.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped.startswith("name = "):
            name = stripped[len('name = "') : -1]  # strip the trailing quote
            continue
        if stripped.startswith("version = ") and name is not None:
            resolved.setdefault(name, stripped[len('version = "') : -1])
    return resolved


def check_version_agreement(
    author_lock: Path,
    deploy_lock: Path,
    *,
    expected: dict[str, str] | None = None,
) -> list[str]:
    """Return human-readable refusals listing every version-skew violation.

    Empty result means the two projects resolve identical, expected pins.
    """
    expected = expected or EXPECTED_PINS
    refusals: list[str] = []
    author = resolved_pins(author_lock)
    deploy = resolved_pins(deploy_lock)

    for pkg, want in sorted(expected.items()):
        a = author.get(pkg)
        d = deploy.get(pkg)
        if a != d:
            refusals.append(
                f"{pkg}: author resolves {a}, deploy resolves {d} -- must be identical"
            )
        elif a != want:
            refusals.append(
                f"{pkg}: both resolve {a}, expected {want} (pyproject pins out of sync)"
            )
    return refusals


# ---------------------------------------------------------------------------
# Cross-file contradiction gate (Problem 5)
# ---------------------------------------------------------------------------


def run_cross_file_contradictions(
    parsed: list[tuple[str, str, Rule]],
) -> list[VerificationError]:
    """Pairwise deploy-time gate across rules of the *same entity class*.

    Rules sharing a ``class_name`` latch the same tracked instances, so their
    check conditions must be jointly satisfiable -- otherwise latching one
    entity would deadlock every such rule.  Two rules of *different* classes
    can never co-latch, so they are not compared.

    All rules use one shared :class:`_Domains`, which makes same-signature op
    calls collide across files, and the pairwise/full-conjunction checks reuse
    the exact Stage-2 z3 encoding.
    """
    from collections import defaultdict

    errors: list[VerificationError] = []

    by_class: dict[str, list[tuple[str, str, Rule]]] = defaultdict(list)
    for filename, rule_id, rule in parsed:
        by_class[rule.class_name].append((filename, rule_id, rule))

    for class_name, members in sorted(by_class.items()):
        if len(members) < 2:
            continue
        for i, (fa, ra, a) in enumerate(members):
            for fb, rb, b in members[i + 1 :]:
                errors.extend(_cross_pair(class_name, fa, ra, a, fb, rb, b))
    return errors


def _cross_pair(
    class_name: str,
    fa: str,
    ra: str,
    a: Rule,
    fb: str,
    rb: str,
    b: Rule,
) -> list[VerificationError]:
    def checks(rule: Rule) -> list:
        return [step.condition for step in rule.steps if isinstance(step, Check)]

    conds_a = checks(a)
    conds_b = checks(b)
    if not conds_a or not conds_b:
        return []

    model = _Domains()
    entity_sym = z3.Int("$entity")
    holders: list[tuple[tuple, Any]] = []
    for src_label, cond in [((fa, ra), c) for c in conds_a] + [((fb, rb), c) for c in conds_b]:
        collected: list[Any] = []
        _collect_leaves(cond, "", model, entity_sym, collected)
        for leaf in collected:
            holders.append((src_label, leaf))
    leaves = holders

    if not leaves:
        return []

    errs: list[VerificationError] = []
    # pairwise: one leaf from each file constraining a shared symbol
    for idx, (who_a, leaf_a) in enumerate(leaves):
        for who_b, leaf_b in leaves[idx + 1 :]:
            if who_a[0] == who_b[0]:
                continue  # same-file pair is already Stage-2's job
            shared = set(leaf_a.symbols) & set(leaf_b.symbols)
            if not shared:
                continue
            if not _sat_with(model, z3.And(leaf_a.formula, leaf_b.formula)):
                signs = ", ".join(sorted(s.__str__() for s in shared))
                errs.append(
                    VerificationError(
                        stage=STAGE_DEPLOY,
                        message=(
                            f"cross-file contradiction ({who_a[0]} + {who_b[0]}): "
                            f"class '{class_name}' rules constrain the same op "
                            f"value(s) ({signs}) in ways that can never hold "
                            f"simultaneously ({_describe(leaf_a.comparison)} vs "
                            f"{_describe(leaf_b.comparison)})"
                        ),
                        path=f"{who_a[0]} + {who_b[0]}",
                    )
                )
                break

    if not errs:
        encoded = [leaf[1].formula for leaf in leaves]
        if not _sat_with(model, z3.And(*encoded)):
            errs.append(
                VerificationError(
                    stage=STAGE_DEPLOY,
                    message=(
                        f"cross-file contradiction ({fa} + {fb}): the conjunction "
                        f"of both class-'{class_name}' rules' check conditions is "
                        f"unsatisfiable ({ra} + {rb} can never co-arm an instance)"
                    ),
                    path=f"{fa} + {fb}",
                )
            )
    return errs


# ---------------------------------------------------------------------------
# Manifest + shipment
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifiedRule:
    """A parsed rule that has passed the full verification pipeline.

    ``source_bytes`` are the original file bytes read verbatim from disk, so
    shipping is byte-faithful: the manifest sha256 and the deployed file are
    always computed over the identical byte sequence (no newline
    normalisation), and an already-deployed file is byte-compared directly.
    """

    rule_id: str
    class_name: str
    filename: str
    source_bytes: bytes
    verified: VerificationResult

    def __post_init__(self) -> None:
        if not self.verified.passed:
            raise ShipRefusedError(
                f"{self.filename}: refusing to ship an unverified rule "
                f"(passed=False, stage {self.verified.stage_reached})"
            )


@dataclass(frozen=True)
class ShipResult:
    """Summary of one (possibly dry-run) shipment attempt."""

    dry_run: bool
    copied: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)
    target_dir: str = ""
    manifest_path: str = ""
    pins: dict[str, str] = field(default_factory=dict)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp_fd, tmp_name = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(tmp_fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def build_manifest(
    rules: list[VerifiedRule],
    *,
    pins: dict[str, str],
    generated_at: str,
) -> dict[str, Any]:
    """Deterministic manifest body for a shipment of ``rules``."""
    manifest_rules: dict[str, dict[str, Any]] = {}
    for rule in rules:
        manifest_rules[rule.filename] = {
            "rule_id": rule.rule_id,
            "class_name": rule.class_name,
            "sha256": _sha256(rule.source_bytes),
            "bytes": len(rule.source_bytes),
        }
    return {
        "schema_version": 2,
        "generated_at": generated_at,
        "pins": dict(sorted(pins.items())),
        "rules": dict(sorted(manifest_rules.items())),
    }


def ship_rules(
    rules: list[VerifiedRule],
    *,
    target_dir: Path,
    author_lock: Path,
    deploy_lock: Path,
    ship_log: Path,
    expected_pins: dict[str, str] | None = None,
    dry_run: bool = True,
) -> ShipResult:
    """Ship ``rules`` into ``target_dir`` atomically.

    Raises :class:`ShipRefusedError` when any fail-stop precondition fails.
    With ``dry_run=True`` (default) nothing is written; the result reports
    exactly which files would be copied and which are already identical.
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    refusals = check_version_agreement(author_lock, deploy_lock, expected=expected_pins or EXPECTED_PINS)
    if refusals:
        raise ShipRefusedError("version skew blocks shipment:\n  - " + "\n  - ".join(refusals))

    pins = dict(expected_pins or EXPECTED_PINS)
    copied: list[str] = []
    unchanged: list[str] = []

    shipped = {rule.filename for rule in rules}
    for rule in rules:
        data = rule.source_bytes
        dest = target_dir / rule.filename
        if dest.exists() and dest.read_bytes() == data:
            unchanged.append(rule.filename)
            continue
        copied.append(rule.filename)
        if not dry_run:
            _atomic_write_bytes(dest, data)

    # Reconcile the deployed directory to the manifested set: any ``*.yaml``
    # that is not part of this shipment is stale / unverified and must not
    # remain armed on the deploy side.  (The boot-time guard refuses on extra
    # files, so shipping also prunes.)  Removed only after every copy landed.
    stale = sorted(
        path.name
        for path in target_dir.glob("*.yaml")
        if path.is_file() and path.name not in shipped
    )
    if not dry_run:
        for name in stale:
            (target_dir / name).unlink(missing_ok=True)

    if dry_run:
        return ShipResult(
            dry_run=True,
            copied=copied,
            unchanged=unchanged,
            pruned=stale,
            target_dir=str(target_dir),
            pins=pins,
        )

    # Manifest LAST: only after every rule file is on disk and stale files are
    # pruned, so the manifest always matches the final directory contents.
    manifest_path = target_dir / MANIFEST_NAME
    manifest = build_manifest(rules, pins=pins, generated_at=_now_iso())
    canonical = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if existing.get("rules") == manifest["rules"] and existing.get("pins") == manifest["pins"]:
                _append_ship_log(ship_log, manifest, copied=copied, unchanged=unchanged, pruned=stale)
                return ShipResult(
                    dry_run=False,
                    copied=copied,
                    unchanged=unchanged,
                    pruned=stale,
                    target_dir=str(target_dir),
                    manifest_path=str(manifest_path),
                    pins=pins,
                )
        except json.JSONDecodeError:
            pass
    _atomic_write_bytes(manifest_path, canonical.encode("utf-8"))
    _append_ship_log(ship_log, manifest, copied=copied, unchanged=unchanged, pruned=stale)

    return ShipResult(
        dry_run=False,
        copied=copied,
        unchanged=unchanged,
        pruned=stale,
        target_dir=str(target_dir),
        manifest_path=str(manifest_path),
        pins=pins,
    )


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _append_ship_log(
    ship_log: Path,
    manifest: dict[str, Any],
    *,
    copied: list[str],
    unchanged: list[str],
    pruned: list[str],
) -> None:
    """Append one line to the append-only shipment log."""
    entry = {
        "shipped_at": _now_iso(),
        "pins": manifest.get("pins"),
        "copied": sorted(copied),
        "unchanged": sorted(unchanged),
        "pruned": sorted(pruned),
        "rules": sorted(manifest.get("rules", {})),
    }
    ship_log.parent.mkdir(parents=True, exist_ok=True)
    with open(ship_log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


__all__ = [
    "MANIFEST_NAME",
    "SHIP_LOG_NAME",
    "STAGE_DEPLOY",
    "ShipRefusedError",
    "ShipResult",
    "VerifiedRule",
    "build_manifest",
    "check_version_agreement",
    "resolved_pins",
    "run_cross_file_contradictions",
    "ship_rules",
]