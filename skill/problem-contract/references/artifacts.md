# Problem-contract artifacts

Both artifacts use UTF-8 JSON and `schema_version: 1`. Keep them compact enough to pass to later model calls without truncating the original problem.

## `contract.json`

Required shape:

```json
{
  "schema_version": 1,
  "status": "ready",
  "top_function": "kernel",
  "interface": {
    "inputs": [],
    "outputs": [],
    "return": null
  },
  "behavior": [
    {
      "claim": "Plain-language observable requirement",
      "source": "problem",
      "confidence": "explicit"
    }
  ],
  "state": {
    "persistent": false,
    "initialization": []
  },
  "numeric": {
    "signedness": [],
    "widths": [],
    "overflow": [],
    "rounding": []
  },
  "boundaries": [],
  "performance_constraints": [],
  "risk_points": [],
  "assumptions": [],
  "unresolved": []
}
```

Use `status: "needs_clarification"` when an unresolved item prevents one defensible behavior. `source` names a model-visible origin such as `problem` or a declared public file. `confidence` is `explicit` or `assumption`; inferred claims must also appear in `assumptions`.

Do not store implementation plans, pragmas, candidate excerpts, hidden-test observations, or reference code in this artifact.

## `test_plan.json`

Required shape:

```json
{
  "schema_version": 1,
  "contract_status": "ready",
  "oracle_strategy": "independent_reference",
  "cases": [
    {
      "id": "zero",
      "category": "boundary",
      "purpose": "Required property to exercise",
      "construction": "How inputs are chosen",
      "comparison": "exact"
    }
  ],
  "properties": [],
  "random": {
    "enabled": true,
    "seeds": [0, 1],
    "cases_per_seed": 8
  },
  "tolerance": null,
  "unresolved": []
}
```

Select applicable structured cases rather than including every possible category. Useful categories include zero, extrema, minimum and maximum legal sizes, impulse, sparse, monotonic, alternating-sign, repeated values, and fixed-seed random cases. Use property or metamorphic tests only when the property follows from the contract.

Exact comparison is the default. A tolerance requires an explicit numeric reason recorded in the contract.
