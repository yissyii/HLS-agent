Design independent functional self-tests using ONLY public problem/interface and the proposed contract.
You have no candidate implementation. Do not request or infer reference implementations, hidden tests or repository files.
Return one JSON object, no prose. Do not emit C++: a trusted renderer builds the driver from this plan.

Required object (no extra keys):
{"schema_version":1,"oracle":{"expression":"integer expression over parameter names","rule_ids":["R1"]},"explicit_cases":[{"inputs":[0],"rule_ids":["R1"]}],"sampling":{"mode":"boundary_random","seed":42,"random_cases":64}}

Use one independent mathematical oracle. Supported syntax is a SMALL Python-expression subset:
- integer/bool literals, parameter names, parentheses;
- + - *, & | ^, << >>, unary + - ~, comparisons == != < <= > >=;
- conditional `value_if_true if condition else value_if_false`, and/or/not (Boolean semantics).
NO calls, attributes, indexing, comprehensions, strings, floats, division, modulo, exponentiation, imports or eval.
Every intermediate magnitude must be below 2**64; shift amounts 0..63. Right shift is arithmetic for negative integers.
There is NO implicit fixed-width overflow. Encode wrapping explicitly with bit masks. Final values must already fit the return type.
For signed W-bit wrap, express the sign conversion explicitly, not a C++ cast.
Use only rules justified by the public specification. If the contract is insufficient, do not invent a numeric oracle.
explicit_cases must have exactly one integer per parameter in interface order and respect the declared domain.
Rule references identify intended checks, not verified coverage or proof of correctness.
Choose exhaustive only when the whole domain fits max_cases; exhaustive requires random_cases=0.
Otherwise boundary_random automatically adds the Cartesian product of each input's min/min+1/-1/0/1/max-1/max (in range), then fixed-seed random attempts.
Explicit/boundary/random duplicates are merged. Never claim random tests prove all inputs correct.
Respect max_cases; budget overflow fails instead of silently truncating. Keep 1..4 inputs and the provided interface unchanged.
