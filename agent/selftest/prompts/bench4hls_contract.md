Extract a composable typed contract from ONLY the public problem and interface.
Candidate code, official tests and outcomes are unavailable. This is a proposed
interpretation, not a semantic certificate. Output exactly one JSON object.

Required top-level fields:
{"schema_version":1,"decision":"ready","nodes":[],"state":[],"outputs":[],"excluded":[],"reasons":[]}
decision is ready, partial, or abstain. partial requires nonempty excluded; ready
has no excluded. Both need outputs and empty reasons. abstain needs reasons and
empty nodes/state/outputs. A useful defined subset is preferable to an unnecessary
whole-task abstention. Missing call/observation conventions must not be invented.

Use only the supplied bool/ap_int/ap_uint scalar interface. Input references i0,
i1,... follow prototype order. true and false are builtin unsigned one-bit values.
State references s0,s1,... and graph references v0,v1,... are assigned consecutively.
Each graph node can reference inputs, OLD state, builtins and earlier nodes only.
No C++, expression strings, Python, expected-value lists or test sequences.

Types have width 1..1024 and signed true/false. Every arithmetic operation wraps
at its result width. Operands must have matching width AND signedness unless an
explicit cast is used. Predicates must be unsigned one-bit values. Logical control
uses these one-bit values; there is no Python and/or returning an integer operand.
JSON integer constants cannot be true/false. For signed constants use the signed
range. All node objects have id and op plus EXACTLY the fields below:

const: width,signed,value (representable integer)
slice: arg,lsb,width (unsigned slice)
concat: args (2..16 refs, first most significant, unsigned result)
add/sub/and/or/xor: a,b (same type; and/or/xor are bitwise)
not: arg (bitwise complement, same type)
eq/lt: a,b (same type; unsigned one-bit result; lt respects signedness)
select: cond,yes,no (one-bit cond; same branch types)
shl/lshr/ashr: arg,amount (constant shift 0..width; lshr unsigned, ashr signed)
cast: arg,width,signed (resize numeric value, then wrap)
rotate: arg,direction,amount (left/right, 0..width)
reverse: arg (reverse all bits, same type)
masked_write: old,data,mask,lane_width (same old/data type; low mask bit controls
  lowest lane; unsigned mask width = data width/lane_width)
table: arg,ones,zeros,dont_care (unsigned arg <=8 bits; lists of integer row numbers
  must partition the ENTIRE input domain exactly; result is one unsigned bit)

Example node syntax: {"id":"v0","op":"const","width":8,"signed":false,"value":1}.
Use slices and concat for field rearrangements. Use table for explicit truth-table
rows, not a hand-expanded Boolean formula. Don't-care rows produce no assertion;
different outputs may independently choose their don't-care values.

Every state has EXACT fields:
{"name":"s0","width":8,"signed":false,"update":"i0","enable":"true","reset":null,"evidence":["P000"]}
reset is null or {"condition":"i1","value":0,"priority":"reset_first"}.
reset_first means reset -> reset value, else enabled -> update, else hold.
enable_first means enabled -> update, else reset -> reset value, else hold.
Choose only the priority and reset value justified by the public text. State reset
must change the stored state, not just mask its output. Active-low controls can use
a not node on a one-bit input. Internal static state needs no explicit state or
clock argument. State starts UNKNOWN; all updates use OLD state simultaneously.
An output reference is not caller-controlled storage. Never reference n0 or other
next-state aliases; no such aliases exist. Limit: 8 states and 128 graph nodes.

Every output has EXACT fields:
{"name":"exact_public_output_name","value":"s0","observe":"after","when":"true","evidence":["P000"]}
observe must be before or after, following the PUBLIC observation convention.
before evaluates the graph using old state; after uses simultaneously updated
state. Combinational nodes are reevaluated in that phase. Do not add a register or
delay merely because the statement mentions a clock. Output type must match exactly.
when is a one-bit reference; false/unknown skips a check. Never disable a defined
case to hide a mismatch. Partial may omit an output with an explicit exclusion.

Evidence is a nonempty list of unique IDs from public_segments. Select supporting
segments; do not invent IDs or copy/paraphrase quotations. IDs establish source
location only. Every state and output needs evidence. Excluded/reasons are bounded
lists of explanations. Preserve unknown semantics. Do not promise completeness.
The framework owns legal input generation, synchronization, coverage and C++.
