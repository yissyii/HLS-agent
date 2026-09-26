# Independent finite-reference gate v1

Recorded 2026-09-26 before implementation. This phase addresses the v4 parity
counterexample without changing the frozen v3/v4 generators or their prompts.
It adds an opt-in admission wrapper, not another model interpretation or RAG.

## Decision and scope

The model still sees only the public problem. Generation writes a draft. A
separate gate checks the draft against a caller-supplied, hash-pinned reference.
Missing reference, invalid artifacts, semantic counterexamples, incomplete
coverage and exhausted comparison budgets cannot export a checked testbench.
Agreement of two model interpretations is never an admission condition.

References are data-only finite transition tables, independent of generated
expressions. Each row maps a reference state and a complete public input tuple
to a next state and every public output. An integer specifies the required
output; null explicitly means unconstrained/unknown, never an inferred zero.
Combinational truth tables are one-state machines. State tables can include an
explicit unknown state that remains unknown until synchronization. Each table
must cover the Cartesian product of its states and the entire typed input
domain, with no duplicates, implicit defaults or omitted output fields.

This supports small finite references across arithmetic, logic and state
families without recognizing task IDs or interpreting English keywords. Larger
input spaces require a different reference adapter; they do not silently fall
back to sampling and retain an exhaustive label.

The gate recompiles the declaration, verifies every lowering artifact, rebuilds
vectors and rendered C++ and verifies the generation receipt. It compares the
compiled graph to the reference by breadth-first exploration of reachable
(reference state, generated state) pairs and all input values. It records a
shortest counterexample prefix, or declares budget exhaustion. A separate pass
checks every assertion in the actual emitted vector sequence. Assertion on an
unconstrained output and omission of a required output are both failures.

Full exploration proves agreement only with the supplied finite reference and
the Python graph semantics. It does not prove that the reference faithfully
represents natural language, that the finite emitted test suite detects every
bad DUT, or that C++/RTL/HLS is equivalent. Those boundaries remain explicit.

## Trust boundary

The caller supplies the expected SHA-256 of the reference file separately.
The table binds exact public bytes, top name, ordered interface and provenance.
Hashes bind identity; they do not authenticate reviewers or prove independence.
Self-declared provenance is recorded as a claim, not promoted into trusted
approval. This phase never produces automatic candidate acceptance or repair
permission, even for a table that claims human review.

Frozen pre-generation evaluation references may be converted to tables for
regression. They retain evaluation-fixture provenance; no fixture or heuristic
slice is relabeled human-reviewed. No reference, gate verdict or counterexample
enters either model extraction or a repair prompt.

## Implementation contract

New core module: `agent/selftest/bench4hls_reference_gate.py`.

- `compile_oracle(raw, problem, top)` accepts public bytes and returns a validated
  finite reference. Schema is described below.
- `compare_finite(contract, oracle, *, max_product_states=4096,
  max_transitions=65536)` returns `equivalent`, `counterexample` or `inconclusive`.
- `assess_generation(problem, top, generation, *, oracle_path=None,
  expected_oracle_sha256=None, max_product_states=4096,
  max_transitions=65536)` is read-only and returns an admission report.
- `review_testbench(problem, top, generation, output, **gate_options)` writes a
  new report directory, preserving its input snapshots. Only `reference_checked`
  writes `checked/selftest.cpp`, for manual review. It never rewrites drafts.
- `generate_guarded_behaviors(problem, top, output, *, model, runtime, deadline,
  max_calls=1024, seed=20260930, **gate_options)` creates `draft/`, then `review/`.
  Model call arguments contain no oracle. This is an opt-in API; default paths
  and frozen sources stay unchanged.

The exact table schema is:

```text
{schema_version: 1, problem_sha256: HEX, top: STRING,
 inputs: [{name: STRING, width: INTEGER, signed: BOOLEAN}, ...],
 outputs: [{name: STRING, width: INTEGER, signed: BOOLEAN}, ...],
 states: [STRING, ...], initial_state: STRING,
 transitions: [{state: STRING, inputs: [INTEGER, ...],
                outputs: {OUTPUT_NAME: INTEGER_OR_NULL, ...},
                next_state: STRING}, ...],
 provenance: {origin: "external_reference" | "evaluation_fixture",
              reference_id: STRING, source_sha256: HEX,
              review_status: "unreviewed" | "fixture_qualified" | "human_reviewed",
              review_record: STRING_OR_NULL}}
```

Limits: at most 256 reference states, 4,096 input tuples, 65,536 table rows;
comparison budgets are explicit positive integers and hard bounded. Admission
statuses: `reference_checked`, `needs_reference`, `abstained`,
`generation_failed`, `invalid_artifact`, `invalid_reference`,
`counterexample`, `inconclusive`. Every report has
`automatic_acceptance_allowed=false`, `repair_feedback_allowed=false`, and
`export_for_review_allowed` true only for `reference_checked`.

## Validation protocol

1. Verify parity versus exactly-two, arithmetic truncation, reset polarity,
   priority, unknown initial state, before/after observation and missing output
   checks. Include mismatches after a multi-step prefix, outside saved stimuli.
2. Check malformed/partial/duplicate tables, wrong public/interface/hash,
   unsigned/signed bounds, tampered graph/vectors/C++, budget exhaustion and
   absent references. Always retain failure status and withhold exports.
3. Test the wrapper using recorded or mocked model responses; verify that a
   shared erroneous primary/blind response is withheld and private reference
   markers never enter prompts. Do not spend live model calls to test plumbing.
4. Replay all 72 prior stress attempts across v3/v4 using pre-existing references.
   Preserve abstentions and denominators. Expected parity rejection is a
   regression result, not new generalization evidence. Any additional semantic
   finding is recorded rather than used to alter references to fit generation.
5. Freeze a source manifest for the new gate and record replay artifacts. The
   108 frozen-test tasks remain untouched. Fresh model generalization evaluation
   requires a subsequent separately frozen protocol, after this gate is stable.

Root owns semantic design, gate implementation and evidence interpretation.
Mechanical test cases, CLI and regression adapters are delegated to
gpt-5.6-terra medium as requested by the user.
