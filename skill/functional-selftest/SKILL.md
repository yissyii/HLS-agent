---
name: functional-selftest
description: Validate an HLS C/C++ candidate by executing specification-derived tests against an independent behavioral oracle before accepting functional correctness.
---

# Functional Self-Test

## Trigger

Use after a candidate is generated or repaired when the task permits local execution and a ready problem contract is available. Do not claim functional validation when no executable oracle or valid property can be constructed.

## Inputs

- Original specification and public interface materials.
- `contract.json` and `test_plan.json` created before candidate inspection.
- Candidate source to compile and execute.
- Explicit toolchain, timeout, and public-feedback permissions from the Agent.

## Procedure

1. Check that the contract is ready and the test plan predates the candidate under test.
2. Construct the simplest independent oracle. Prefer ordinary bounded C++ or Python arithmetic; do not copy candidate control flow, HLS pragmas, or optimized data structures.
3. Materialize only applicable boundary, structured, property, and fixed-seed random cases from the test plan.
4. Compile the candidate with an isolated generated testbench.
5. Execute with a bounded timeout and compare outputs programmatically.
6. On a mismatch, minimize the report to the first actionable case and smallest relevant index while preserving enough inputs to reproduce it.
7. Return the result to the Agent. The Agent owns repair count and stage transitions.

Never mark PASS from model inspection alone. Passing self-tests does not imply synthesis success or hidden-test correctness.

## Result contract

Return one structured result:

```json
{
  "schema_version": 1,
  "status": "passed",
  "stage": "selftest",
  "tests_run": 20,
  "oracle": "independent_reference",
  "first_failure": null,
  "artifacts": []
}
```

Failure status is one of `compile_failed`, `runtime_failed`, `mismatch`, `oracle_unavailable`, or `invalid_contract`. A mismatch includes the case ID, reproducible input, first mismatch location, expected value, and actual value. Do not return the entire build log when a concise compiler diagnostic is available.

## Retrieval contract

Retrieval is optional and may use only `patterns` and validated `failures`, with no more than four evidence items. It may help choose tests or diagnose a failure, but expected outputs must come from the specification-derived oracle or a valid property, never from retrieved answer code.

## Failure modes

- Deriving expected output from the candidate.
- Reusing the candidate's algorithm in the oracle.
- Generating tests only after observing candidate behavior.
- Treating an invalid or ambiguous contract as a test failure.
- Returning hidden oracle material beyond the task's feedback permission.

## Validation status

Integrated behind a default-off policy flag. The generated testbench is frozen before candidate 0, then compiled and executed in an isolated Vitis CSim workspace before public validation. Synthetic repair-loop tests pass; benchmark precision and benefit remain unvalidated.
