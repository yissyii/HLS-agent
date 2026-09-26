# Named semantic rules (experimental v3)

`bench4hls_contract_generation.generate_rules` extracts a small public behavior
declaration and deterministically lowers it through the existing obligations,
typed graph, stimulus and C++ rendering pipeline. The pilot selects it with
`--representation rules`. It is not a competition selftest mode or the default
generator, and its output remains `generated_unreviewed`.

The closed language has three rule kinds:

| Rule | Model supplies | Program supplies |
| --- | --- | --- |
| `sum` | 2–8 unsigned public input names | Operand widening before addition; output truncation when needed |
| `boolean` | A condition over unsigned one-bit public inputs | Bounded parsing of `!`, AND, XOR, OR and parentheses; graph references |
| `up_counter` | Inclusive cyclic bounds, optional enable and reset | Output type, updated-state observation and unknown initial state |

Each exact public output name must appear in `outputs`, with `rule`, source
`evidence` IDs and `reason`. Resolved rules require evidence and `reason: null`;
unresolved outputs use `rule: null` and an explicit reason. Scope is derived by
the compiler. Missing outputs cannot masquerade as complete coverage. Evidence
IDs bind to the current problem's semantic slices; they are not correctness
labels. Full public text remains visible. No retrieval is used by this route.

For example, a public two-operand sum with output `total` can be represented as:

```json
{
  "schema_version": 3,
  "outputs": {
    "total": {
      "rule": {"kind": "sum", "inputs": ["first", "second"]},
      "evidence": ["s0001"],
      "reason": null
    }
  }
}
```

Use the actual output/input names and slice IDs from the supplied problem. The
sum rule is unsigned modular arithmetic in the public output type. It cannot
stand in for carry-only output, subtraction or saturation. Boolean rules cannot
reference other outputs, call functions or represent a partial input domain.
The counter supports nonzero bounds/reset values but cannot represent decrement,
load, saturation, old-state observation or cascaded state. Arrays, streams,
floating-point and general state machines remain outside this frontend.

One primary compiler retry and one blind independent extraction are allowed.
Agreement between extractions is not proof: they can share an interpretation
error. Independent public reference checks and native execution are separate
evaluation steps and never become generation feedback in this pilot.

Reproduce the inspected nine-task development experiment with a new directory:

```powershell
python -B tools/run_bench4hls_obligations_pilot.py --split validation --evaluation-purpose development --tasks Prob016 Prob024 Prob026 Prob034 Prob036 Prob037 Prob038 Prob064 Prob069 --output output/rules_development_NEW --representation rules --repeats 3 --workers 1 --max-calls 1024 --seed 20260927
```

The historical split is `validation`, but these tasks were inspected before v3
and are development reuse. This command does not establish held-out performance.
The seed controls stimulus generation, not model sampling. Use `obligations`
instead of `rules` for the v2 control arm with otherwise identical parameters.

The run preserves source/config/problem hashes, all attempts, original schema-3
`specification.json`, `lowered_obligations.json`, `expanded_graph.json`, vectors,
coverage, `selftest.cpp`, blind review and reported model usage. Independent
extraction artifacts are under `independent_000`. Failed and abstained attempts
remain in the denominator; some do not have executable artifacts.

The preregistered bounds and measurements are in
[the v3 design](../../report/design/bench4hls_named_rules_v3.md).
