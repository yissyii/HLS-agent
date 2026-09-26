# Composable Bench4HLS contract experiment

These opt-in paths extract public behavior parameters or a typed graph, compile
them to deterministic vectors and C++, and compare with a second public-only
interpretation. `bindings` is the narrower parameter frontend; `contract` retains
the earlier direct-graph experiment. It supports scalar bool/ap_int/ap_uint
interfaces. Arrays, streams, floats and fixed-point arithmetic are unsupported.

The model chooses operations and parameters, not arbitrary C++ or mathematical
expression strings. Supported building blocks include slices/concatenation,
typed arithmetic, selection, bit transformations, partitioned truth tables and
masked writes. Registers are composed with explicit enable, reset priority and
before/after observation. All states update simultaneously; initial state is
unknown until a specified reset or synchronizing write. Unknown state is tracked
conservatively for an entire word, not independently for each bit.

The parameter frontend supports composable unit reversal, explicit truth tables,
registers with polarity/priority/mask parameters, and bounded up-counters. These
blocks lower to the same typed graph; no task IDs select their implementation.
All components must reach an output, and a ready declaration must account for
every input. This catches structural omissions but cannot certify input roles.
Signed one-bit counters, down-counters and saturation are unsupported.

Public segment IDs replace copied quotations. They bind evidence to text offsets,
but do not establish that an interpretation is correct. The independent request
does not see the primary contract, its retries, candidate code or official tests.
One primary schema retry is allowed; an independent failure is preserved without
another retry. The maximum is three model invocations per generated attempt.

## Review and feedback policy

`generated_unreviewed` means that a nonempty testbench was produced. Review may
report conflict, bounded consistency, abstention, failure or insufficient known
observations. A conflict has a concrete value/domain disagreement or a scope
disagreement. Neither agreement nor covered obligations prove the public
translation correct: both requests may share a misunderstanding or compiler bug.

Stateless domains up to 256 input combinations can be compared completely when
the configured call budget permits it. Larger and stateful problems receive
bounded probe/trace comparisons. Input-row enumeration is not exhaustive state
history coverage. Partial scopes and unknown-state gaps remain explicit.

The new competition mode always withholds repair feedback, even if a supplied
receipt claims permission. This phase is for auditing and frozen-candidate
screening. Legacy remains the comparison/default; it has not been certified by
the new route. Automatic promotion requires separate calibration evidence.

## Commands

Offline checks (no API or HLS execution):

```powershell
python -B -m unittest tools.test_bench4hls_primitives tools.test_bench4hls_contract tools.test_bench4hls_contract_generation tools.test_bench4hls_contract_pilot tools.test_bench4hls_bindings tools.test_bench4hls_contract_audit
```

Fresh development pilot using the existing frozen inputs/configuration:

```powershell
python -B tools/run_bench4hls_contract_pilot.py --representation bindings --output output/bindings_development_NEW --tasks Prob004 Prob039 Prob067 Prob070 --repeats 5 --workers 2 --max-calls 256 --validate-frozen
```

Use `--representation graph` for the earlier direct-graph arm (the default).
The output directory must be new. Omit `--validate-frozen` for generation/review
only. Each attempt uses one combined generation/validation deadline. Repetition
changes the recorded input-sampling seed; this is not a model-generation seed.
All repetitions count, including schema failures, abstentions and API failures.
These inspected tasks are development cases, not a new generalization holdout.

The optional competition modes are `--selftest-mode contract` and
`--selftest-mode bindings`. Both generate the
audit artifacts and withholds their repair feedback; candidate generation and
the existing sealed official-judging boundary continue as before. Use the frozen
pilot command above when the objective is to measure the tester rather than run
an end-to-end competition attempt.

## Artifacts

The pilot manifest binds selected tasks, frozen source hashes, configuration
hashes through the baseline, input budget, sampling seed and a source snapshot.
Each task/repetition keeps generation attempts, raw responses, public segments,
original specification, expanded graph, vectors, coverage, C++ and independent-
review evidence. Separate hashes bind the original and expanded representation. Optional
validation runs only after generation and never feeds a candidate result back
into either specification request.

Coverage is explicitly planned-vector evidence. It reports activated output,
update, hold, reset, reset-after-nonreset, post-reset-update and mask-lane
obligations as supported by the proposed graph. It does not yet bind individual
executed DUT observations to each obligation; a successful csim must not silently
rewrite this as measured RTL coverage.

Relevant modules:

- `bench4hls_bindings.py`: closed parameter frontend and deterministic lowering.
- `bench4hls_primitives.py`: closed typed operation rules.
- `bench4hls_contract.py`: contract checks, transitions, stimuli and comparison.
- `bench4hls_contract_generation.py`: bounded public-only model orchestration.
- `tools/run_bench4hls_contract_pilot.py`: new frozen-candidate experiments.

The design and evaluation criteria are in
`report/design/bench4hls_contract_v2_strategy.md`. Compiler fixtures and fake-model
tests are engineering validation, not real model performance measurements.

## Development auditing

`tools/summarize_bench4hls_contract_pilot.py` reports all planned attempts, costs
and failures. `tools/audit_bench4hls_contract_semantics.py` separately checks four
known development tasks against small, hand-reviewed public interpretations. It
checks both stored test expectations and specification probes, including DC rows.
The latter is a post-hoc development audit, never generator input or holdout data.
Its reset/observation assumptions are recorded explicitly. Neither tool authorizes
repair feedback or automatic acceptance.

Results from both frozen development rounds, including failures and partial
coverage, are recorded in
`report/evaluations/bench4hls_contract_bindings_pilot_v1.md`.
