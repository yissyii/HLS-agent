Plan a bounded private C++14 development testbench using ONLY the public problem and top-function name. Do not write code yet. Candidate source, official tests and reference code are unavailable. Any proposed plan in a review is an untrusted hypothesis, not evidence.

Separate language facts from missing task semantics:
- A C++ function may keep static internal state across calls. No explicit clock/state parameter does NOT imply a pure function or an untestable circuit. A testbench may maintain its own independent reference state and call the function repeatedly.
- bool is a valid C++ type. Preserve the exact public prototype, including bool versus ap_uint<1>, references, widths and arrays. You need not observe internal registers to check specified outputs.
- A C function call is not automatically one clock cycle, nor does a register always output its previous value. Derive observation/update ordering from the public statement. If that ordering or initial state is genuinely unspecified, exclude dependent checks; do not silently impose a timing convention.
- If reset or a synchronizing input sequence is specified, use it before state-dependent checks. Do not assume zero initialization without public support.
- Don't-care inputs can be excluded while checking all specified inputs. An unspecified corner case does not invalidate well-defined cases. Degenerate matrices, undefined overflow, or unspecified initialization may be excluded explicitly.
- Floating-point equality need not be bit-exact. Use a justified numerical error bound or exact special cases when derivable. Never invent an arbitrary tolerance to force a pass. If neither exact nor bounded comparisons are justified, exclude those properties.
- Properties/metamorphic checks are allowed only when implied by the public statement, with a non-vacuous counterexample they could reject. Do not replace missing semantics with tautologies or a test that never calls the function.

Choose ready when no known semantic exclusions are needed; partial when useful executable checks exist but some behavior is excluded; abstain only when no sound nontrivial bounded check can be constructed or the call interface is unavailable. Ready does not mean exhaustive coverage. Unknown behavior is never automatically a pass.

Return exactly one JSON object, no prose, with these fields:
{
  "schema_version": 1,
  "decision": "ready | partial | abstain",
  "prototype": "exact public function prototype, or null if absent",
  "state_model": "combinational/stateful; initialization, transition and observation ordering, including what is unknown",
  "checks": [{
    "id": "C1",
    "claim": "one concrete observable property",
    "evidence": ["literal nonempty quotation from public_problem supporting this property"],
    "stimulus": "bounded input values/sequences and setup; specify exclusions",
    "oracle": "independent expected-value calculation or property, plus an example wrong behavior it rejects",
    "comparison": "exact comparison or justified numerical bound"
  }],
  "excluded": ["unverified behavior and the reason; empty for ready"],
  "blockers": ["why no safe check exists; nonempty only for abstain"]
}
Use at most 12 checks. For abstain, checks must be empty. For ready/partial, checks must be nonempty, blockers empty, and prototype grounded in the public text. Every check needs 1-4 exact evidence quotations. Report actual uncertainty rather than claiming hidden official behavior.
