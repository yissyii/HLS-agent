Extract what each public output DOES from the complete public_problem and its
source-bound semantic slices. Return ONE JSON object with EXACT top-level keys:
{"schema_version":4,"outputs":{}}
outputs contains EVERY exact public output name from output_template. Each entry
has EXACT keys behavior,evidence,uncertainty. Evidence uses supplied semantic
slice IDs, never copied quotations or guessed IDs.

Describe the behavior even when you are unsure whether an implementation library
can execute it. The program checks implementability separately. Do not discuss
library limitations, select components, or emit supported/unsupported flags.
No C++, casts, widths, IDs, aliases, test vectors, decision, reason or observe
fields outside the layouts below. Port types come from the supplied interface.

When the public meaning is determined: behavior is one of the objects below,
evidence is nonempty, uncertainty is null. When required facts are absent or
conflicting, set uncertainty to exactly:
{"category":"missing_information","detail":"the specific missing public fact"}
or {"category":"ambiguous","detail":"the competing interpretations"}.
Then behavior may be null, or a complete well-formed description if useful.
Uncertainty always remains unresolved. Do not invent values to complete a shape.
Do not use uncertainty merely because you think a library lacks an operation.

Behavior layouts (exact fields):

ARITHMETIC
{"kind":"arithmetic","operation":"add","operands":["input_a","input_b"],"overflow":"wrap"}
operation is add, subtract or multiply. operands is 2..8 exact public input names.
Subtraction and multiplication are evaluated left to right. Repeated operands
are allowed only if the stated behavior repeats them. overflow is wrap or
saturate, as specified. A wider sum output includes carry; a narrower modular
output wraps. Record the public arithmetic meaning without assigning internal
widths or truncating intermediate expressions. Carry-only outputs require their
own logical condition; they are not the complete arithmetic sum.

LOGIC
{"kind":"logic","expression":"input_a && (!input_b || input_c)","domain":"total"}
Write the output condition over exact PUBLIC INPUT names, 0/1, !, &/&&, ^, |/||
and parentheses. Precedence is ! then & then ^ then |. Operators can be chained
and nested; an expression may combine any number of the available input names
within the bounded expression size. No calls, attributes, comparisons or output
references. Inline the input condition of another output when needed. Preserve
all mode conditions and overrides instead of assuming input order. domain is
total when every input combination has defined behavior, partial when the public
text deliberately leaves some input combinations unspecified or don't-care.
Missing behavior is not a convenient constant. Inputs and outputs in this layout
describe one-bit logical values; wider numeric relations use another layout.

STATE
{"kind":"state","observe":"after","initial":null,"reset":null,"enable":null,
 "update":{"operation":"add","amount":1},"boundary":{"at":7,"next":0},"load":null}
The numbers in this layout illustrate field types, not the current problem.
One call corresponds to one stated state update. Describe the following facts:
- observe: after or before, according to which state's value the public output
  exposes. Do not silently change observation time.
- initial: null for an unknown/unspecified initial state before synchronization,
  or an integer only when a definite initial state is explicitly stated.
- reset: null if absent, otherwise exactly {"input":"reset_port","active":1,
  "value":0,"priority":"reset_first"}. Input names are exact public names;
  active is 0 or 1. value is the public stored reset state. priority is reset_first
  if reset wins over enable, or enable_first if enable wins over reset.
- enable: null if always active; otherwise exactly {"input":"enable_port","active":1}.
  Disabled updates hold state. Preserve active-low controls when present.
- update: exactly {"operation":"add","amount":1}, using add or subtract and
  a positive integer amount. This is the ordinary state update away from a
  boundary, when enabled and without a higher-priority control.
- boundary: null if no equality boundary is stated; otherwise exactly
  {"at":7,"next":0}. It says that enabled OLD state equal to at becomes next,
  instead of the ordinary update. Record nonzero destinations just as directly.
  A saturating boundary has next equal to at; a cyclic boundary can jump to a
  different value. A public statement "reset OR state equals U -> L; otherwise
  increment" describes reset value L, boundary at U/next L, ordinary add 1.
- load: null if absent; otherwise exactly {"input":"load_port","active":1,
  "value_input":"data_port","priority":"load_first"}. priority is load_first
  if loading wins over reset, or reset_first if reset wins over loading.

If a required reset value, control priority, transition or output meaning cannot
be determined, record that fact in uncertainty instead of assigning a default.
An unspecified initial state alone can be represented by initial:null; it does
not prevent later synchronization by a specified reset.

OTHER
{"kind":"other","description":"the concrete publicly required behavior"}
Use this for behavior whose meaning cannot be described by the above layouts,
such as a stream protocol or unrelated multi-state transition. Describe the
actual requirement, not a supposed inability of a library to implement it.

The full public text is authoritative for extraction. Source evidence and
heuristic slice labels are not correctness proofs. Do not infer missing facts
from candidate code, official tests or another interpretation. Resolve outputs
independently and preserve every public output, including unresolved ones.
