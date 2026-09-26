Extract a behavior declaration using ONLY the supplied public problem, interface
and public_segments. Output exactly one JSON object. Candidate code, official
tests and outcomes are unavailable. Your interpretation remains unverified.

Exact top-level fields:
{"schema_version":1,"decision":"ready","components":[],"outputs":[],"excluded":[],"reasons":[]}
ready covers all public inputs and outputs, with empty excluded/reasons.
partial has nonempty excluded, empty reasons and at least one useful output.
abstain has nonempty reasons and empty components/outputs. Use partial or abstain
when the available behaviors cannot express the public semantics. Do not invent
initial state, timing conventions, reset, or convenient output values.

Input references i0,i1,... follow the supplied prototype order, NOT guessed role
names. Components are c0,c1,... in order. Data references can be public inputs or
earlier components. Every component has id,kind and EXACTLY the fields below.
No graph nodes, arithmetic expressions, C++, sample sequences, or expected outputs.

reverse_units: input,unit_width,evidence
Reverse the order of equal-width units, retaining the bit order within each unit.
unit_width=1 reverses bits; unit_width=8 reverses bytes. This is a permutation, not
a numerical endian conversion with additional operations. Input must be unsigned,
unit_width must divide its width. Up to 64 units, except bit reversal allows more.

truth_table: inputs,ones,zeros,dont_care,evidence
inputs is a list of 1..8 distinct unsigned one-bit references in MSB-first order.
The integer row index is formed in that order. ones/zeros/dont_care are integer
row lists that exactly partition every possible row. Copy the public function's
truth table faithfully. Do not derive an approximate Boolean formula. A don't-care
row has NO required output value: do not put it in ones or zeros. Separate outputs
may reuse this component only if their defined values/domains are the same.

register: data,enable,reset,mask,evidence
data sets the stored width and signedness. Each function call performs one state
transition. enable=null means always enabled; otherwise use {"input":"iN","active":0}
or active:1 with an unsigned one-bit PUBLIC control input. active is the asserted
logic level. It is not the inactive level or the value stored on reset.
reset=null means there is no reset. Otherwise reset has exact fields input,active,
value,priority. input is a one-bit public control, value is the integer state after
reset, priority is reset_first or enable_first. For active-low reset use active:0.
reset_first: reset asserted -> reset value; else enable asserted -> data; else hold.
enable_first: enable asserted -> data; else reset asserted -> reset value; else hold.
The reset changes stored state, not only the visible output. A reset control must
not be reinterpreted as a data enable. Both controls may be present simultaneously.
mask=null means replace the whole word. Otherwise mask is {"input":"iN","lane_width":8}.
Mask bit zero controls the least-significant lane. Its width must equal data width
divided by lane_width. Set mask bits write data; unset bits preserve old stored bits.
Masking applies only to writes, not to reset. With mask and no separate enable, use
enable:null; a zero mask naturally holds the state. State is initially UNKNOWN.

counter: width,signed,lower,upper,enable,reset,evidence
Increment old state by one when enabled; if old state equals upper, wrap to lower.
lower < upper and both fit the declared type. enable/reset have the same shapes as
register. Reset value must be in [lower,upper]. No step, decrement, saturation,
initial, or extra wrap field exists. Do not use this kind for another behavior.

Outputs have EXACT fields:
{"name":"exact_public_output_name","value":"c0","observe":"after","evidence":["P000"]}
value is an input or component reference. observe is before or after according to
the public call/observation semantics: before sees old state, after sees updated
state. State transitions are simultaneous: a register fed by another register
captures the OTHER register's OLD value. Combinational components are evaluated
in the output observation phase. Clock wording alone does not imply extra delay.
Output types must match. Never reference an output name as writable state.

Every component must contribute to an output. ready must use every public input
in reachable behavior; omission requires explicit partial scope. This structural
check does not establish semantic correctness. Limit 32 components and 8 states.
evidence is a nonempty list of unique, supplied public segment IDs supporting the
declaration. Do not invent IDs or use quotations as IDs. Evidence binds locations;
it does not excuse an unsupported interpretation. The framework constructs typed
operations, test sequences, checks and C++ from the declared parameters.
