Extract a functional behavior declaration using ONLY public_problem, interface,
public_segments and the provided output_template. Return ONE JSON object:
{"schema_version":2,"components":[],"outputs":{}}
The outputs object MUST have EVERY exact public output key in output_template.
Do not emit decision, excluded, reasons, graph nodes, C++, or component IDs.
The program derives complete/partial/abstain scope from your per-output entries.
Your interpretation remains unverified. Do not invent initial state, missing
reset values, timing, input restrictions or convenient expected values.

Every output entry has EXACT keys value, observe, evidence, reason:
- Represented: {"value":"c0","observe":"after","evidence":["s0001"],"reason":null}
- Unresolved: {"value":null,"observe":null,"evidence":["s0001"],"reason":"specific unsupported or ambiguous behavior"}
Use [] evidence only if no relevant source segment exists for an unresolved output.
Never delete an output key. Use all functional constraints that CAN be checked.
A truth-table don't-care row leaves only that row unconstrained: other defined
rows MUST remain checked, independently for every public output. A requirement
about minimal SOP/POS form does not prevent checking the specified truth values.
You may share a value component between outputs if their defined behaviors match.
If all outputs are unresolved, components must be [].

Input references are the exact strings in public_input_references, e.g. in:reset,
in:d. Do not emit i0 aliases, guessed roles or bare port names. The program assigns
c0,c1,... to components in list order. A component may reference public inputs or
EARLIER cN components only. No forward/self references. Every component must
contribute to a represented output. Every public input must contribute for complete
scope; an unused input must not be disguised by an invented behavior.

Each component has kind and evidence PLUS exactly the kind-specific fields below.
evidence is a nonempty list of unique supplied semantic-slice IDs (s0001 etc.).
IDs bind exact public text, not semantic truth. Labels are heuristic; read the
full original problem and paragraph context offsets when interpreting a slice.

reverse_units: input,unit_width
Reverse equal-sized units while keeping bits within each unit. Unsigned source;
unit_width divides source width. 1 means bit reversal, 8 means byte reversal.
Up to 64 units except unit_width=1. No extra type fields.

truth_table: inputs,ones,zeros,dont_care
inputs: 1..8 distinct unsigned one-bit references, FIRST is MSB. Integer row lists
ones/zeros/dont_care must form an exact, disjoint partition of all 2^N rows.
Preserve defined rows literally rather than deriving an approximate formula.
Dont-care rows return unknown and are not asserted, even if a particular minimal
expression happens to choose a value. Distinct outputs may differ on these rows.

register: data,enable,reset,mask
State has data's type, starts UNKNOWN, and updates on each function invocation.
enable=null means always enabled; otherwise {"input":"in:enable","active":1}.
The input can also be an earlier unsigned one-bit component. active is integer0/1.
reset=null or {"input":"in:reset","active":1,"value":0,"priority":"reset_first"}.
Active-low reset uses active:0; value is stored state after reset, not its polarity.
priority reset_first: reset -> value; else enable -> data; else hold.
priority enable_first: enable -> data; else reset -> value; else hold.
Reset must alter stored state, not merely the visible output. Reset is not enable.
mask=null or {"input":"in:byteena","lane_width":8}. Unsigned mask width equals
state width/lane_width. Mask bit0 writes the least-significant lane; unset lanes
preserve old state. Masking affects writes, not reset. Zero mask naturally holds.
Data/control/mask may refer to earlier components with compatible types.

counter: width,signed,lower,upper,enable,reset
Up-count by ONE when enabled; old==upper wraps to lower. lower<upper, all values
fit the type, reset value within [lower,upper]. enable/reset have register shapes.
State starts UNKNOWN. This kind cannot express down-counting, saturation or loading
arbitrary data. Do not fake those behaviors with an up-counter.

const: width,signed,value
Representable typed integer; width1..1024, signed boolean. JSON booleans are NOT
integer values. A 1-bit predicate constant is unsigned width1 value0 or1.
cast: input,width,signed
Numeric resize to the declared type, then wrap. Widen operands BEFORE adding when
a public sum must retain carry. Adding at a narrow width already loses that carry.
slice: input,lsb,width
Unsigned contiguous bits; bit0 is least significant, lsb+width<=source width.
concat: inputs
2..16 references, FIRST occupies highest bits. Unsigned result, totalwidth<=1024.
binary: operation,a,b
operation is add/sub/and/or/xor/eq/lt. Operands must have IDENTICAL width/signedness;
use cast when needed. add/sub wrap at that type. eq/lt return unsigned one bit.
select: cond,yes,no
cond unsigned1, yes/no same type. Returns yes if cond1, no ifcond0.
shift: input,direction,amount
direction left/logical_right/arithmetic_right, constant integeramount0..width.
Logical right requires unsigned input, arithmetic right requires signed input.

For each represented output, value is an exact input reference or component.
Its type must equal the public output type. observe=before sees OLD state;
observe=after sees UPDATED state. All states update simultaneously from old state.
A register fed by another register captures the latter's OLD value. A combinational
expression of state is evaluated in the chosen output phase. Clock wording alone
does not imply an extra call delay. Never use an output port as writable state.
Maximum32 components and8 states. Unsupported behavior must have an explicit
per-output reason; do not misrepresent it to pass type checks. The framework owns
input sequences, expectations, coverage bookkeeping, and C++ construction.

public_domain_constraints, when present, are exact literal public row-list constraints
with source spans and an explicit input order. Every listed output must remain
unasserted on those rows, independently of any simplified expression. Preserve
these rows in truth_table.dont_care. An empty constraints list means the narrow
parser found no supported literal form; it does NOT mean the domain is total.
