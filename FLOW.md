# From Rule Generation to Shipping — End-to-End Flow

This document describes the **actual, current** flow in this repo (`JJ-LLM`, the
author side) from a human querying for a rule, through LLM generation,
validation, verification, and shipping into the detection repo
(`../JJ-object-detection`, the deploy side).

Everything below is taken from the current source. Key files:

| Concern | File |
| --- | --- |
| TUI entry / rules table | `src/llm_agent/app.py`, `src/llm_agent/ui/main_screen.py` |
| LLM translation (Gemini) | `src/llm_agent/gemini_translator.py` |
| Grammar spec for the prompt | `src/llm_agent/grammar_spec.py` |
| Pre-save validation | `src/llm_agent/rule_validator.py`, `src/llm_agent/app_integration.py` |
| Allowed classes (from deploy config) | `src/llm_agent/detection_classes.py` |
| Author rule directory | `LM-rules/` (repo root) |
| Verify all rules | `run_checks.py` |
| Verify + ship | `ship_rules.py` |
| Verification pipeline | `src/sih/verification/` (`_stage0`, `_stage1_dedup`, `_stage2`, `_stage4`, `_ship`) |
| Rule engine / runtime semantics | `src/sih/rules/engine.py`, `src/sih/rules/ops.py`, `src/sih/rules/opdefs/`, `src/sih/input/framebuffer.py` |

## Repo layout (two repos)

- **JJ-LLM** (this repo): *only* writer of the deployed rule set.
- **JJ-object-detection** (sibling `../JJ-object-detection`): the deployed
  system. It owns `config.toml` (the class list contract) and the target
  `LM-rules/` directory that JJ-LLM ships into.

The author directory is `LM-rules/` here (read by `run_checks.py`);
the deploy directory is `../JJ-object-detection/LM-rules` (written only by
`ship_rules.py`). They are separate on purpose: failing rules stay on the
author side and can never be force-shipped.

## 1. Generation — the TUI asks Gemini

1. The user types a natural-language query in the Textual app
   (`MainScreen.on_input_submitted`, `main_screen.py:93`).
2. A filename-safe rule id is produced from the query slug + uuid:
   `_make_rule_id` (`main_screen.py:22-25`, e.g. `pothole_persist_conf_a1b2c3`).
3. The call runs in a worker thread so the UI stays live
   (`main_screen.py:112-114`) → `app_integration.create_and_save_rule`
   (`app_integration.py:12`).
4. `RuleTranslator` (`gemini_translator.py:45`) builds a system prompt that
   pins the model to the strict v2 grammar:
   - `build_grammar_spec()` (`grammar_spec.py:76`) — top-level keys, condition
     grammar, value expressions, the **14 ops with each argument's kind**
     (derived live from `sih.rules.ops.OPS` via `inspect.signature`), and the
     `on_exit` grammar.
   - MANDATORY keys (`description`, `track`, `schema_version: 2`, `rule_id`,
     `steps`) and FORBIDDEN legacy keys/shapes
     (`gemini_translator.py:63-74`), plus a worked `_EXAMPLE_RULE`
     (`gemini_translator.py:17-42`).
   - The allowed classes come from `load_rule_classes()`
     (`detection_classes.py:25`) — the sibling repo's `config.toml`
     `[classes]` map, falling back to `{crack, pothole}`.
5. Gemini (`gemini-3.6-flash`, temperature 0) returns YAML text with retry +
   exponential-backoff on 408/429/5xx (`gemini_translator.py:91-121`).

## 2. Pre-save validation gate (`RuleValidator`)

`app_integration.create_and_save_rule` runs `validator.parse_rule(yaml_code)`
(`app_integration.py:26-27`) before anything is written. `RuleValidator`
(`rule_validator.py:19`) rejects with a `RuleSyntaxError` if:

- the content is not parseable YAML,
- the top level is not a mapping,
- any value is a non-primitive (dates/datetimes, etc.) or any dict key is not
  a string (`rule_validator.py:56-72`),
- `description` is missing/empty (`rule_validator.py:30-34`),
- `track` is not exactly `{class: ...}` or the class is not in the allowed
  list (`rule_validator.py:36-47`),
- the engine parser itself rejects the rule
  (`engine.parse_rule`, `rule_validator.py:49-52`) — schema_version, unknown
  top-level keys, step shapes, condition operators, op names, `on_exit`
  actions.

On success the file is written as `LM-rules/<rule_id>.yaml`
(`app_integration.py:34-35`). On failure nothing is saved and the UI reports
"Oops, the AI made a grammar mistake" (`main_screen.py:124-129`). The rules
table also lists already-saved YAML files directly (`main_screen.py:69-79`).

## 2b. Stage 1 — semantic near-duplicate detection (informational)

Because Gemini now generates rules live in the TUI, every generation call risks
producing a rule that is a near-duplicate of one already in `LM-rules/` (same
intent, slightly different threshold). Stage 1
(`verification/_stage1_dedup.py`) catches exactly that:

- Two rules of the same `track.class` are compared by the **abstract
  op-call signatures** appearing in their `check` conditions
  (`abstract_signatures`, `_stage1_dedup.py:93`). A signature is the op name +
  argument structure with every numeric literal collapsed to a placeholder
  (`metric`/window/`$entity` keep their identity; threshold numbers do not).
  Numeric *threshold* literals live on the comparison, not in the signature, so
  two rules differing only in `0.6 -> 0.75` collide while rules using different
  ops do not. `on_exit` payloads are excluded (they repeat across rules).
- Similarity = **Jaccard overlap** over the signature sets
  (`jaccard`, `_stage1_dedup.py:106`); a pair is flagged at
  `DEFAULT_SIMILARITY_THRESHOLD = 0.5` (`_stage1_dedup.py:41`).
- Entry points: `find_for_candidate(candidate, existing)` for the TUI
  (compare a newly-generated rule against the rest of `LM-rules/`), and
  `find_in_set(rules)` for `run_checks.py` / `ship_rules.py` (pairwise over the
  verified set).
- **It never gates.** Findings are `SimilarityFinding` records (carried on
  `VerificationResult.similarities`, `_errors.py:105`) surfaced to a human
  review gate — the TUI prints a chat-log note naming the lookalike, the shared
  ops, and which thresholds differ; `run_checks.py` / `ship_rules.py` print an
  informational "Stage 1" section. A flagged rule never changes `passed`, the
  exit codes, or the shipment (see §5/§6).

## 3. What lands in `LM-rules/`

Each shipped candidate is one `schema_version: 2` YAML rule. The reference
shape (`LM-rules/pothole_persist_conf.yaml`):

```yaml
schema_version: 2
rule_id: pothole_persist_conf
description: >
  A pothole instance appears across 5 frames with a mean confidence >= 0.60 ...
track:
  class: pothole
steps:
  - check:
      gte:
        left: { op: persisted_for, entity: "$entity", window: { frames: 5 } }
        right: 5
  - check:
      gte:
        left: { op: avg_in_window, entity: "$entity", metric: confidence, window: { frames: 5 } }
        right: 0.6
  - on_exit:
      leave_frames: 1
      do:
        - emit:
            event_type: pothole_persistent_confirmed
            confidence: { op: avg_in_window, entity: "$entity", metric: confidence, window: { frames: 5 } }
            payload: { tracker_id: "$entity", frames_seen: { op: persisted_for, entity: "$entity", window: { frames: 60 } } }
        - mark: success
```

`LM-rules/` also holds two **synthetic test fixtures**
(`contradictory_area.yaml`, `unparseable_broken.yaml`) that are verification
proof-cases — they are never shipped and never gate the exit code.

## 4. Verification pipeline (`sih.verification`)

`run_verification_pipeline(rule_yaml, filename=...)`
(`verification/__init__.py:37`) is the single entry point used by both scripts.
It is **fail-fast**, ordered, and deliberately has no LLM in the loop
(teammate integrations call it with generated YAML).

**Stage 0 — structural** (`_stage0.py:356`):
- yaml parsing; a document that is not valid YAML returns a Stage-0 error
  (`_stage0.py:358-364`).
- a recursive walker validates the whole tree **with precise YAML paths**
  (`_validate`, `_stage0.py:296`): top-level key whitelist, `schema_version`
  must be 2, `rule_id` non-empty and (when a `filename` is given) equal to the
  file stem, `track` must be `{class: ...}`, `steps` non-empty list of
  single-key `check`/`on_exit`, condition operators, and `on_exit` shape
  (`do` list with `emit`/`mark`, `leave_frames` positive int).
- op calls are checked **deeply**: unknown op name, unknown kwargs, missing
  required args, and static argument-type mismatches via `inspect.signature`
  on the real op functions (`_check_op_call`, `_stage0.py:111-172`).
- on zero errors the engine parser builds the authoritative AST
  (`parse_rule`, `_stage0.py:374-383`) so the walker can never drift from the
  engine grammar.

**Stage 2 — formal / contradiction (z3-solver)** (`_stage2.py:319`):
All `check` conditions must be *simultaneously* satisfiable before `on_exit`
can fire, so the conjunction is checked with z3:
1. a single comparison whose threshold lies outside the op's value domain
   (e.g. `persisted_for(frames 5) >= 6`);
2. pairwise contradictions between two comparisons over the **same op-call
   signature** (e.g. `area > 40000` and `area < 40000`);
3. the full boolean formula (including `all`/`any`/`not` nesting).

Op value domains are encoded conservatively (`confidence`/`avg` in `[0,1]`,
`persisted_for` in `[0, window]`, non-negative areas/speeds, ...)
(`_stage2.py:108-126`). A 10s z3 timeout treats "unknown" as no-contradiction
(`_stage2.py:312`).

**Stage 4 — property-based behaviour** (`_stage4.py:545`):
Drives the **actual engine** (`RuleRegistry.load_dir` + `tick`) over
deterministic synthetic entity histories pushed into the framebuffer singleton,
asserting the engine's documented invariants:
1. unresolvable/insufficient data never raises;
2. check completion is monotonic within a record's lifetime;
3. a record fires only when all checks are armed **and**
   `leave_frames` consecutive absent frames elapsed (the boundary is tested
   exactly) (`_stage4.py:199-272`).
Also built-in **boundary cases** around the rule's real numeric thresholds
(`_stage4.py:319`) and a **mutation report** that perturbs each threshold
±10% to classify it as inert/brittle/sensitive — **report-only, never gating**
(`_stage4.py:496-537`).

**Errors** are machine-actionable `VerificationError(stage, message, path)`
(`_errors.py:19`), e.g. `[stage0] steps[0].check.gte.left.op: unknown
operation 'is_visble' (...)` — designed to be fed back into an LLM retry
prompt.

**Stage 1 — semantic near-duplicate detection** (`_stage1_dedup.py`): runs
*after* Stage 0 over already-parsed rules of the same class; it is
informational only and never sets `passed=False`. See §2b for the definition
and the review-gate semantics.

## 5. `run_checks.py` — verify everything in `LM-rules/`

- Discovers all `*.yaml` in `LM-rules/` except the synthetic fixtures
  (`run_checks.py:64-66`).
- Runs the pipeline per rule; invalid YAML / unloadable rules are reported as
  `UNPARSEABLE` without aborting the run (`run_checks.py:86-100`).
- Prints per-stage PASS/FAIL/SKIPPED plus the mutation diagnostics
  (`run_checks.py:102-131`).
- **Exit code is non-zero iff any discovered rule fails** (`run_checks.py:144`).
  The synthetic fixtures are reported but never gate (`run_checks.py:147-150`).
- After verification, a **Stage 1** section lists near-duplicate pairs over the
  verified rules — informational, never affects the exit code
  (`run_checks.py:165-182`).

## 6. `ship_rules.py` — verify and deploy

`ship_rules.py` is the deploy path (`--ship` actually deploys; default is a
dry-run; `--quiet` prints exit-code only).

Per rule (`ship_rules.py:95-139`): read bytes verbatim → decode → run the full
verification pipeline → `parse_rule` on success. **Only passing rules** become
`VerifiedRule` candidates (`_ship.py:234-255`; `source_bytes` keep shipping
byte-faithful). Failing rules are listed as `EXCLUDED FROM SHIP` and remain in
the author `LM-rules/` unchanged (`ship_rules.py:193-199`). A **Stage 1**
section then lists near-duplicate pairs among the verified set — informational,
never blocks (`ship_rules.py:210-227`).

Then, in order (`_ship.py`):

1. **Cross-file deploy gate** (`run_cross_file_contradictions`,
   `_ship.py:125`): rules that share a `class_name` latch the same instances,
   so their check conditions must be jointly satisfiable. Reuses the Stage-2
   z3 encoding with one shared `_Domains` so same-signature op calls collide
   across files. A contradiction blocks the whole shipment
   (`ship_rules.py:201-208`).
2. **Version agreement** (`check_version_agreement`, `_ship.py:91`): the
   resolved `numpy` and `z3-solver` versions in both repos' `uv.lock` files
   must be identical and equal to `EXPECTED_PINS = {"numpy": "2.5.3",
   "z3-solver": "5.1.0.0"}` (`_ship.py:59`). Any skew raises `ShipRefusedError`
   before a single file is touched.
3. **Idempotent atomic copy** (`ship_rules`, `_ship.py:316`): each rule is
   written to a hidden `.tmp` in the target dir and renamed into place
   (`_atomic_write_bytes`, `_ship.py:275`); a target file whose bytes already
   match is left untouched (`_ship.py:347-349`).
4. **Prune stale files**: any `*.yaml` in the target not in this shipment is
   removed after all copies land (`_ship.py:358-365`) — so unverified/stale
   rules can never remain armed on the deploy side.
5. **`MANIFEST.json` written last** (`_ship.py:377-398`): per-file
   `sha256`/`bytes`/metadata plus the pins, only after every file is on disk,
   so the manifest always matches disk.
6. **Append-only `SHIP_LOG.jsonl`** records every shipment
   (`_append_ship_log`, `_ship.py:416`).

Target: `../JJ-object-detection/LM-rules`. Currently that directory holds
`crack_da1c3e.yaml`, `crack_large_area.yaml`, `pothole_persist_conf.yaml`, and
`MANIFEST.json`.

**Fail-stop guarantees** (`_ship.py:1-33`): no skip mode, no allow-list, no
partial copies, manifest-last, byte-stable output. `ShipRefusedError` → exit
code 1 (`ship_rules.py:220-222`).

## 7. What the deployed side sees

The detection repo boots by loading `LM-rules/` from disk:
- `MANIFEST.json` declares the exact rule set (per-file sha256 + the pinned
  `numpy`/`z3-solver` versions).
- Each rule YAML is parsed by the same `sih` engine
  (`RuleRegistry.load_dir` + `tick`, `engine.py:405-428`).
- At runtime the input layer pushes detections into the global framebuffer
  singleton (`src/sih/input/framebuffer.py:68`); each rule latches new
  BoT-SORT `tracker_id`s of its `track.class`, advances one record per
  instance, completes `check` steps monotonically, and fires `on_exit`
  (`do: [emit, mark]`) after `leave_frames` absent frames when fully armed
  (`engine.py:431-518`). An `emit` produces an `EmittedEvent` with the
  rule_id/event type/confidence/payload.

## Sequence diagram

```
  user query ──► TUI (MainScreen)
                   │  rule_id = slug_uuid6
                   ▼
             RuleTranslator ──► Gemini (temp 0, grammar_spec prompt)
                   │                                           
                   ▼                                           
             RuleValidator (primitives / description / track.class /
                             engine.parse_rule) ── pass/fail
                   │ pass                                     
                   ▼                                          
             LM-rules/<rule_id>.yaml        (author side)
                   │
                   ▼  run_checks.py / ship_rules.py
             run_verification_pipeline ── Stage 0 (structural)
                                        ── Stage 1 (near-dup; informational)
                                        ── Stage 2 (z3 contradictions)
                                        ── Stage 4 (property via real engine)
                   │  verified rules only
                   ▼
             cross-file z3 gate (same class) + uv.lock version gate
                   │
                   ▼
             atomic copy → ../JJ-object-detection/LM-rules
             prune stale → MANIFEST.json (last) → SHIP_LOG.jsonl
                   │
                   ▼
             deploy boot: RuleRegistry.load_dir + tick per frame
             (framebuffer singleton ← detection input feed)
```

## Environment note

The verification stages import `z3` (`sih.rules.verification._stage2`, `_ship`),
which loads a native `libz3.dll` from `z3-solver`. On this Windows machine
Windows Smart App Control was blocking the unsigned DLL; with Smart App
Control disabled, `z3 5.1.0` loads and `run_checks.py` runs the full pipeline
end to end.