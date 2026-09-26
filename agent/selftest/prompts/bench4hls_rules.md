Read the complete public_problem and its source-bound public_segments. Return ONE
JSON object with EXACT top-level keys:
{"schema_version":3,"outputs":{}}
outputs must contain EVERY exact public output name from output_template.
Each output entry has EXACT keys rule, evidence, reason. A represented output has
a rule, nonempty semantic slice IDs, and reason:null. An unresolved output has
rule:null, a specific reason string, and relevant evidence IDs (or [] if absent).
Do not emit component IDs, widths, casts, aliases, C++, expected-value lists,
decision, excluded, observe, or other fields. Program owns type lowering, component
references, stimuli and C++ rendering. Evidence identifies text; it is not proof.

There are exactly THREE rule kinds:

1. Unsigned arithmetic sum
{"kind":"sum","inputs":["exact_input_name","another_input_name"]}
Use 2..8 PUBLIC unsigned input names; repeated operands are permitted only if
the public behavior adds that operand repeatedly. The program widens operands
BEFORE addition as required by the public output width, then represents the sum
in that output type. A wider output retains the carry. A narrow output gets the
modular sum. Do not manually split sum/carry, truncate intermediates, or specify
casts. This rule does not represent subtraction, saturation, comparisons or
signed arithmetic. It does not automatically represent a carry-only output.

2. Named Boolean condition
{"kind":"boolean","expression":"input_a && (!input_b || input_c)"}
Output and referenced inputs must all be unsigned ONE BIT. Use exact public
INPUT names, literals 0/1, !, &/&&, ^, |/|| and parentheses only. Operators have
precedence ! then & then ^ then |. AND/OR operate on Boolean values. No function
calls, attributes, arithmetic, assignments, comparisons or output references.
Write each output's own condition directly from public requirements. To describe
an output that depends on another logical output, inline that output's input
condition; never assume port order or use a numeric truth-table index.
For example, if a public statement says a signal is active only when both a
request and a permission are asserted, its condition is request && permission.
This example is a language capability, not evidence for the current task.
Use parentheses to make grouping explicit. Retain manual overrides and every
specified condition. Unknown or don't-care inputs must not be replaced by a
convenient fixed truth value; this rule has no partial-domain syntax, so an output
requiring a partial domain must be explicitly unresolved in this version.

3. Post-update cyclic up-counter
{"kind":"up_counter","lower":0,"upper":7,"enable":null,
 "reset":{"input":"reset_name","active":1,"value":0,"priority":"reset_first"}}
Counter width and signedness come from its public output. One call performs one
state update, and this rule exposes the UPDATED state. Initial state is UNKNOWN.
The exact operation while enabled is: if OLD state equals upper, next=lower;
otherwise next=OLD+1. Both bounds are inclusive, with lower<upper. Nonzero lower
and nonzero reset values ARE supported. Thus a public rule that says "when state
reaches U, return to L; otherwise increment" is exactly this cyclic operation;
it does not require an extra conditional-reset component.
enable:null means always enabled; otherwise {"input":"public_bool_name","active":0_or_1}.
reset:null means no known reset; otherwise EXACT fields input,active,value,priority.
Reset stores the stated value in the state, not only in the visible output.
priority:"reset_first" means reset wins over enable; "enable_first" means enable
wins over reset. Preserve the priority supported by public evidence. Disabled
calls hold state. Reset values must be within [lower,upper]. Preserve explicit
reset polarity and value; do not confuse an enable with reset.
This counter cannot represent decrementing, saturation, data loading, old-state
observation, cascaded counters or unrelated conditional transitions. Use an
unresolved output rather than pretending such behavior is this rule.

Read capabilities accurately before declaring behavior unrepresentable. Still
preserve real uncertainty: do not invent missing reset values, timing, restrictions
or rules. Every public input must contribute for complete scope; the compiler
will reject an unused input instead of letting it hide omitted behavior.
Outputs may be resolved independently, but none may be omitted. Maximum eight
outputs and 32 internally lowered components; keep Boolean conditions simple.

The full original text is authoritative within this extraction. Semantic role
labels are heuristic, and literal public_domain_constraints are source-bound
guards rather than an exhaustive statement of the domain. No candidate design,
official testbench or other model interpretation is available or permitted.
