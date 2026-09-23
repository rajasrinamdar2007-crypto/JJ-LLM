# Verification lives in JJ-LLM only — problem inventory

Status snapshot this file records (July 2026): the verification closure
(`sih/verification/**`, `sih/rules/**`, `sih/common/{types,buffer,logging}`,
`sih/input/framebuffer`) has been **copied** into `JJ-LLM/src/sih`, the closure
imports verify inside JJ-LLM (`uv run python -c "import sih.verification ..."`),
and `run_checks.py` runs there with behavior identical to the source repo.
Deps added to JJ-LLM: `numpy>=2.0.0`, `z3-solver>=5.1.0.0`. The originals in
`JJ-object-detection` were not touched (copy-only; hashes unchanged).

The intended end-state this document evaluates:

```
JJ-LLM  (verification authority)
  LM-rules/        discovered real rules  +  synthetic fixtures (report-only)
  run_checks.py    stage 0/2/4 gates per discovered rule; fixtures never gate
        |
        |  ship: verified rule set + manifest (sha256 per file)
        v
JJ-object-detection  (deployed runtime, consumes shipped set only)
  LM-rules/        loaded at boot; boot-time manifest assert
```

Each problem below is a real failure mode of that split, with the concrete
guard. All guards are proposals; none is implemented yet.

---

## Problem 1 — Verified set drifts from deployed set

Verification happens on the JJ-LLM copy; deployment reads JJ-object-detection's
LM-rules. If the two trees ever disagree (a manual edit on either side, a
dropped file in the ship step, a different glob order, a rename), the set that
was verified is not the set that runs. This is the single biggest risk: every
stage-0/2/4 verdict becomes meaningless for the rules that actually execute.

Guard: ship a **manifest** alongside the rules — one line per file:
`<sha256>  <filename>`. Detection asserts at boot: every file in its LM-rules ==
the manifest exactly (no extra, no missing, no changed bytes); refuse to load
otherwise.

## Problem 2 — A genuinely-broken discovered rule keeps the harness red

Today the run in JJ-LLM exits 1 because a **discovered real rule** fails to
load:
- `dark_02a8dc.yaml` → `UNPARSEABLE` (`track must be {class: ...}, got None`).
- `crack_large_area` and `pothole_persist_conf` → PASS.

That exit 1 is correct: it is the honest signal that one discovered rule cannot
be verifiedcars. The danger is the response to it. Do **not** "fix" it by:
- reclassifying it as a synthetic fixture (synthetics are hardcoded,
  report-only, and never the real rules),
- adding an allow-list of "known-broken" rules to silence it.

Either turns the honest gate into noise and lets a genuinely broken rule ship
unverified. Resolution must be real: fix the rule or remove it from
`LM-rules/` (a git-visible quarantine commit). Keep the gate red until then.

## Problem 3 — Direct edits to the deployed LM-rules bypass verification

Anyone (or any future automation) writing a rule straight into
`JJ-object-detection/LM-rules/` after a shipped manifest means an **unverified**
rule loads at boot. The manifest assert (Problem 1) catches it, but only if the
detection side actually fails stops.

Guard: the boot-time assert must be fail-stop (refuse to arm any rules when the
manifest mismatches), and the deployed LM-rules dir must be write-protected
source-control-wise: changes to it only land through the ship step. The
detection side already runs the full pipeline at load inside its rule-engine
task; keep that as the second gate so a mismatch can never be "sneaked" past
the manifest.

## Problem 4 — Synthetic fixtures must stay permanently, loudly, report-only

The dummy rules (`contradictory_area`, `unparseable_broken`, plus the real
`dark_02a8dc` case) exist precisely to prove the gates catch broken rules. Two
traps:
1. Someone considers a green-full-run "pipeline fixed" — but green happens
   only when synthetics are excluded, which is by design. Green must mean
   "all discovered rules pass", never "synthetics passed".
2. Someone deletes or "fixes" the synthetic fixtures because they look like
   noise, silently removing the only permanent proof the gates actually fire.

Guard: keep synthetics in a clearly separated hardcoded section that never
gates the exit code, and treat any run that reports synthetics as expected
failures with delerately-failing-by-design. Do not allow a
"make synthetic pass" mode.

## Problem 5 — Cross-file contradictions are never checked

Stage 2 checks for contradictions **within a single rule file** (that is what
`contradictory_area` proves). Nothing checks that two separate discovered
rules contradict each other (e.g. one rule demanding `area > 40000` for
"crack" and another demanding `area < 30000` for "large crack" both firing on
the same entity). The pipeline is per-file by construction.

Guard: add a pairwise cross-file contradiction pass over the discovered set
before shipping (feed pairs of rules through the same z3 structural
solver), and record it in the manifest.

## Problem 6 — Mutation verdicts are synthetic-sample based, not live-data based

`mutation_report(..., samples=80)` runs each mutated threshold against a small
synthetic history. Numbers like `changed 5/89 -> sensitive threshold` or
`changed 0/89 -> possibly inert threshold` describe that synthetic stream only.
A "possibly inert" verdict (0/89) is a warning that the threshold never fires
on that sample set — it is not an acceptance criterioncars.

Guard: treat mutation output strictly as a triage/diagnostic signal; never as
a gate. Document that "passes the harness" means "loads, no contradictions,
activates on the synthetic stream" — not "performant on live video".

## Problem 7 — The harness must never crash on one bad file

A file that is not valid YAML, or that parses to a rule with `track: None`,
must be reported as a distinct failure value (`UNPARSEABLE`) and the run must
continue with the other files. `run_checks.py` made copies of this closure
and the harness already catches `RuleSyntaxError`/YAML errors and keeps
looping — keep that contract on any future wiring of the closure into the LLM
agent code. The same must hold at runtime: one bad rule must never crash the
object-detection process (load-time hard-fail with a clear message, not a
process abort).

## Problem 8 — z3/numpy version skew between the two repos

Verification dependencies `z3-solver` and `numpy` are now pinned in both
repos. If one repo upgrades (e.g. a z3 point release changes an unsat/sat
verdict, or numpy 1.x -> 2.x changes float boxing in the framebuffer closure),
the JJ-LLM verdicts no longer reproduce on the detection side.

Guard: pin the same exact versions in both `pyproject.toml` files and record
`(z3-solver, numpy, closure hash)` in the shipped manifest; refuse to ship if
the detection side's lockfile doesn't match the manifest.

## Problem 9 — Ship must be atomic and idempotent

Writing rules + manifest into the deployed tree must never leave a partially
written YAML, a truncated rule, or a duplicate on re-send. A plain copy that
fails halfway gives the boot-time loader a malformed file.

Guard: write each rule to `<name>.tmp.<pid>` then rename into place (atomic on
the same volume); write the manifest last; make re-send idempotent (same
manifest = no-op). Ship and reject-on-mismatch are the only allowed writers of
the deployed LM-rules dir.

## Problem 10 — Exit-code semantics

`run_checks.py` exits non-zero iff any **discovered** LM-rules rule fails a
gate. Synthetic fixtures and mutation diagnostics never affect the exit code.
Today `EXIT=1` because of `dark_02a8dc`. This contract is the point of the
harness: it must stay exactly this strict, and no "exit-code override" or
"allow-list" should ever be added. The dashboards should show the two
categories separately (discovered vs synthetic) so a red run is never
"explained away" as fixture noise.

---

## Decision record (as of the migration)

- JJ-LLM is now the single verification authority for LM-rules; copies were
  made, originals untouched (hash-verified identical in the source repo).
- `run_checks.py` + closure + `LM-rules/` + `uv` deps (`numpy`, `z3-solver`)
  all land and run inside JJ-LLM; behavior identical to the source harness.
- `lm_agent` (JJ-LLM's own package) is unchanged; the verification closure
  currently ships as a standalone harness (`run_checks.py`), not yet wired into
  `rules_store`/`rule_validator` — wiring is a separate, later change and must
  respect Problems 4, 7 and 10 above.

Problems 1, 2, 5 and 8 are the ones to resolve before anything ships from
JJ-LLM to JJ-object-detection: without the manifest + fail-stop boot assert +
cross-file contradiction pass + pinned identical z3/numpy, a green JJ-LLM run
does not actually guarantee the deployed set is verified.
