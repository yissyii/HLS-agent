"""Independent, prospective behaviour-stress fixtures.

This module deliberately contains its own small reference interpreter and C++
printer.  It is not coupled to the production generators or graph controls.
"""
from __future__ import annotations

import itertools
import random


def _port(name: str, width: int, direction: str) -> dict[str, object]:
    return {"name": name, "width": width, "direction": direction}


def _prototype(ports: list[dict[str, object]]) -> str:
    args = []
    for p in ports:
        typ = f"ap_uint<{p['width']}>"
        args.append(f"{typ} &{p['name']}" if p["direction"] == "out" else f"{typ} {p['name']}")
    return "void TopModule(" + ", ".join(args) + ");"


def _case(case_id, family, scope, text, ports, **parameters):
    parameters["ports"] = ports
    return {"case_id": case_id, "family": family, "expected_scope": scope,
            "public_problem": text + "\n\n" + _prototype(ports), "top": "TopModule",
            "parameters": parameters}


def build_cases(seed: int) -> list[dict[str, object]]:
    """Build the fixed-size prospective suite; the seed only chooses safe variants."""
    rng = random.Random(seed)
    cases = []
    sum_specs = [
        ([[1, 3], [2, 3]], "wide", False), ([[3, 1], [3, 2]], "narrow", False),
        ([[1, 2, 3], [2, 1, 3]], "wide", False), ([[2, 3, 1], [3, 1, 2]], "narrow", True),
    ]
    for n, (width_choices, output_kind, repeated) in enumerate(sum_specs, 1):
        widths = rng.choice(width_choices)
        # Shuffle names, leaving the operand list and the public signature aligned.
        names = ["left", "right", "carry_in"][:len(widths)]
        rng.shuffle(names)
        outw = sum(widths) if output_kind == "wide" else max(widths)
        ports = [_port(x, w, "in") for x, w in zip(names, widths)] + [_port("sum", outw, "out")]
        phrase = "Add all unsigned input values."
        if output_kind == "narrow": phrase += " Keep only the low output-width bits of the total."
        else: phrase += " The output is wide enough to retain the complete carry."
        if repeated: phrase += f" The operand named {names[0]} is intentionally used twice: add {names[0]} again before producing sum."
        cases.append(_case(f"Sum{n:02d}", "sum", "supported", phrase, ports,
                           kind="sum", operand_names=names, operand_widths=widths,
                           output="sum", output_width=outw, repeated_first=repeated))
    logic_specs = [
        (3, "xor3"), (3, "majority3"), (4, "mode_override"), (5, "gated"),
    ]
    logic_names = [["a", "b", "c"], ["red", "green", "blue"],
                   ["mode", "warm", "cool", "manual"], ["gate", "p", "q", "r", "s"]]
    for n, ((count, rule), names) in enumerate(zip(logic_specs, logic_names), 1):
        rng.shuffle(names)
        ports = [_port(x, 1, "in") for x in names] + [_port("result", 1, "out")]
        if rule == "xor3": text = "result is one exactly when an odd number of the three Boolean inputs are one."
        elif rule == "majority3": text = "result is one when at least two of the three Boolean inputs are one."
        elif rule == "mode_override": text = (f"When {names[0]} is one, result uses {names[1]} AND {names[2]}; when {names[0]} is zero, it uses {names[1]} OR {names[2]}. "
                                                   f"{names[3]} forces result to one.")
        else: text = (f"result is one when {names[0]} is one, either {names[1]} is one or {names[2]} is zero, "
                      f"and at least one of {names[3]} and {names[4]} is one.")
        cases.append(_case(f"Logic{n:02d}", "boolean", "supported", text, ports,
                           kind="logic", rule=rule, input_names=names, output="result"))
    counter_specs = [(1, 1, "reset_first"), (0, 1, "enable_first"), (1, 0, "enable_first"), (0, 0, "reset_first")]
    counter_names = [("reset", "enable", "count"), ("clear_n", "run", "value"), ("restart", "advance_n", "tick"), ("rst_n", "step_n", "phase")]
    for n, (reset_active, enable_active, priority) in enumerate(counter_specs, 1):
        low = rng.randrange(1, 3); high = low + rng.randrange(2, 5)
        reset_value = rng.randrange(low, high + 1)
        width = max(1, high.bit_length())
        reset_name, enable_name, output_name = counter_names[n - 1]
        inputs = [_port(reset_name, 1, "in"), _port(enable_name, 1, "in")]
        rng.shuffle(inputs)
        ports = inputs + [_port(output_name, width, "out")]
        text = (f"{output_name} has unknown initial state. {reset_name} is active when {reset_name} equals {reset_active}; {enable_name} is active when {enable_name} equals {enable_active}. "
                f"On a call, {'reset takes priority over enable' if priority == 'reset_first' else 'enable takes priority over reset'}. "
                f"A reset sets {output_name} to {reset_value}. An enabled update increments {output_name}, wrapping from {high} to {low}; otherwise it holds. "
                "The output is the updated state after this call.")
        cases.append(_case(f"Count{n:02d}", "cyclic_counter", "supported", text, ports,
                           kind="counter", low=low, high=high, reset_value=reset_value,
                           reset_active=reset_active, enable_active=enable_active, priority=priority,
                           reset_name=reset_name, enable_name=enable_name, output=output_name,
                           output_width=width, initial_unknown=True))
    negatives = [
        ("Negative01", "down_counter", "A down counter has unknown initial state; active-high reset sets it to 3, and every other call decrements, wrapping 1 to 4.",
         {"kind": "down", "low": 1, "high": 4, "reset_value": 3}),
        ("Negative02", "saturating_counter", "A saturating counter has unknown initial state; active-high reset sets it to 1, enabled calls increase it until it holds at 5.",
         {"kind": "saturating", "low": 1, "high": 5, "reset_value": 1}),
        ("Negative03", "load_counter", "A loadable counter has unknown initial state. Reset has priority: active-high reset sets it to 2. Otherwise active-high load copies any 3-bit data value. Otherwise active-high enable changes old count 6 to 2, and changes every other old count by adding one with 3-bit wraparound. Otherwise it holds.",
         {"kind": "load", "low": 2, "high": 6, "reset_value": 2}),
    ]
    for cid, fam, text, meta in negatives:
        if meta["kind"] == "load": ports = [_port("reset",1,"in"),_port("load",1,"in"),_port("data",3,"in"),_port("enable",1,"in"),_port("count",3,"out")]
        elif meta["kind"] == "saturating": ports = [_port("reset",1,"in"),_port("enable",1,"in"),_port("count",3,"out")]
        else: ports = [_port("reset",1,"in"),_port("count",3,"out")]
        cases.append(_case(cid, fam, "unsupported", text + " The output is the updated state.", ports, **meta, output="count", initial_unknown=True))
    ports = [_port("a",1,"in"), _port("b",1,"in"), _port("result",1,"out")]
    cases.append(_case("Negative04", "partial_boolean", "unsupported", "For inputs except a=1 and b=1, result is a XOR b. When both inputs are one, the output is explicitly unconstrained.", ports, kind="partial_logic", output="result"))
    ports = [_port("reset",1,"in"), _port("enable",1,"in"), _port("count",3,"out")]
    cases.append(_case("Under01", "underspecified_counter", "underspecified", "count has unknown initial state. Reset is active-high, but its reset value is unspecified; no output value may be inferred until that missing value is supplied.", ports, kind="under_reset", output="count", initial_unknown=True))
    ports = [_port("a",1,"in"), _port("b",1,"in"), _port("result",1,"out")]
    cases.append(_case("Under02", "underspecified_boolean", "underspecified", "The Boolean inputs are provided, but the rule for result is unspecified; no output constraint is defined.", ports, kind="under_logic", output="result"))
    assert len(cases) == 18
    return cases


def _inputs(case): return [p for p in case["parameters"]["ports"] if p["direction"] == "in"]


def _input_value(case, values, name):
    return values[[p["name"] for p in _inputs(case)].index(name)]


def _output_width(case):
    return next(p["width"] for p in case["parameters"]["ports"] if p["direction"] == "out")


def _counter_row(case, **by_name):
    return [by_name[p["name"]] for p in _inputs(case)]


def probes(case: dict[str, object]) -> list[list[int]]:
    p = case["parameters"]; kind = p["kind"]
    if kind in {"sum", "logic", "partial_logic", "under_logic"}:
        widths = [x["width"] for x in _inputs(case)]
        return [list(row) for row in itertools.product(*[range(1 << w) for w in widths])]
    # Begin unknown, then synchronize by reset, then test holds, simultaneous controls,
    # two wraps, and an interior reset.  Inputs are in declared public order.
    if kind == "under_reset": return [[0, 0], [1, 0], [0, 1]]
    ra = p.get("reset_active", 1); ea = p.get("enable_active", 1)
    ri, ei = 1 - ra, 1 - ea
    if kind == "down": return [[0], [1]] + [[0]] * 10 + [[1], [0]]
    if kind == "load":
        return [[0,0,0,0], [1,0,0,0], [0,0,0,0], [0,1,p["high"],1], [1,1,3,1], [0,0,0,1]] + [[0,0,0,1]] * 12
    if kind == "saturating":
        return [[0, 0], [1, 0], [0, 0], [0, 1]] + [[0, 1]] * 12 + [[1, 0], [0, 0]]
    rn, en = p["reset_name"], p["enable_name"]
    row = lambda r, e: _counter_row(case, **{rn: r, en: e})
    period = p["high"] - p["low"] + 1
    base = [row(ri, ei), row(ra, ei), row(ri, ei), row(ri, ea), row(ra, ea), row(ri, ea)]
    return base + [row(ri, ea)] * (period * 2 + 2) + [row(ra, ei), row(ri, ei), row(ri, ea)]


def step(case: dict[str, object], inputs: list[int], state, mutation: str | None = None):
    """Direct reference semantics.  Unknown state produces no asserted output."""
    p = case["parameters"]; kind = p["kind"]; out = p["output"]
    if mutation is not None and mutation not in mutation_names(case): raise ValueError("unknown mutation")
    if kind == "sum":
        values = inputs
        if mutation == "omitted_operand": values = values[:-1]
        # The omission fault removes only the final declared operand.  It does
        # not silently remove the separately specified repeated first operand.
        total = sum(values) + (inputs[0] if p["repeated_first"] else 0)
        if mutation == "missing_carry": total &= (1 << max(p["operand_widths"])) - 1
        return {out: total & ((1 << p["output_width"]) - 1)}, state
    if kind in {"logic", "partial_logic", "under_logic"}:
        if kind == "under_logic": return {}, state
        if kind == "partial_logic" and inputs == [1, 1]: return {}, state
        if p.get("rule") == "xor3": value = sum(inputs) & 1
        elif p.get("rule") == "majority3": value = int(sum(inputs) >= 2)
        elif p.get("rule") == "mode_override": value = int(inputs[3] or ((inputs[1] and inputs[2]) if inputs[0] else (inputs[1] or inputs[2])))
        elif p.get("rule") == "gated": value = int(inputs[0] and (inputs[1] or not inputs[2]) and (inputs[3] or inputs[4]))
        else: value = inputs[0] ^ inputs[1]
        if mutation == "omitted_condition": value = int(bool(inputs[0]))
        if mutation == "invert_output": value = 1 - value
        return {out: value}, state
    if kind == "under_reset": return {}, None
    if state is None:
        # Synchronize only when this implementation actually performs a defining
        # operation.  A reset-polarity mutant can therefore synchronize on the
        # opposite level, exactly as its static C++ state does.
        if kind == "load": defining = bool(inputs[0] or inputs[1])
        elif kind in {"down", "saturating"}: defining = bool(inputs[0])
        else:
            reset_on = _input_value(case, inputs, p["reset_name"]) == p.get("reset_active", 1)
            if mutation == "resetpolarity": reset_on = not reset_on
            enable_on = _input_value(case, inputs, p["enable_name"]) == p["enable_active"]
            action = ("reset" if reset_on else ("enable" if enable_on else "hold")) if p["priority"] == "reset_first" else ("enable" if enable_on else ("reset" if reset_on else "hold"))
            defining = action == "reset"
        if not defining: return {}, None
    if kind == "down":
        next_state = p["reset_value"] if inputs[0] else (p["high"] if state == p["low"] else state - 1)
    elif kind == "saturating":
        next_state = p["reset_value"] if inputs[0] else (min(p["high"], state + 1) if inputs[1] else state)
    elif kind == "load":
        if inputs[0]: next_state = p["reset_value"]
        elif inputs[1]: next_state = inputs[2] & ((1 << _output_width(case)) - 1)
        elif inputs[3]: next_state = p["low"] if state == p["high"] else state + 1
        else: next_state = state
    else:
        reset = _input_value(case, inputs, p["reset_name"])
        enable = _input_value(case, inputs, p["enable_name"])
        reset_on = reset == p["reset_active"]; enable_on = enable == p["enable_active"]
        if mutation == "resetpolarity": reset_on = not reset_on
        if p["priority"] == "reset_first": action = "reset" if reset_on else ("enable" if enable_on else "hold")
        else: action = "enable" if enable_on else ("reset" if reset_on else "hold")
        if action == "reset": next_state = p["reset_value"]
        elif action == "enable":
            upper = p["high"] + 1 if mutation == "wrongwrap" else p["high"]
            next_state = p["low"] if state == upper else state + 1
        else: next_state = state
    next_state &= (1 << _output_width(case)) - 1
    return {out: next_state}, next_state


def mutation_names(case: dict[str, object]) -> list[str]:
    if case["expected_scope"] != "supported": return []
    kind = case["parameters"]["kind"]
    if kind == "sum":
        # Carry truncation is invisible at a deliberately modular output, so use
        # an omitted operand there; both mutants have a known differing probe.
        return ["omitted_operand" if case["parameters"]["repeated_first"] or case["parameters"]["output_width"] == max(case["parameters"]["operand_widths"]) else "missing_carry"]
    if kind == "logic": return ["omitted_condition"]
    if case["parameters"]["priority"] == "enable_first":
        p = case["parameters"]; mask = (1 << _output_width(case)) - 1
        return ["wrongwrap" if ((p["high"] + 1) & mask) != p["low"] else "resetpolarity"]
    return ["resetpolarity"]


def _ctype(port): return f"ap_uint<{port['width']}>"


def cpp_source(case: dict[str, object], mutation: str | None = None) -> str | None:
    p = case["parameters"]; kind = p["kind"]
    if kind in {"partial_logic", "under_logic", "under_reset"}: return None
    if mutation is not None and mutation not in mutation_names(case): raise ValueError("unknown mutation")
    ports = p["ports"]; sig = _prototype(ports).replace(";", "")
    ins = [x["name"] for x in _inputs(case)]; out = p["output"]
    if kind == "sum":
        terms = ins[:-1] if mutation == "omitted_operand" else ins[:]
        if p["repeated_first"]: terms.append(ins[0])
        expr = " + ".join(f"(unsigned){x}" for x in terms)
        if mutation == "missing_carry": expr = f"(({expr}) & {(1 << max(p['operand_widths'])) - 1})"
        body = f"    {out} = {expr};"
    elif kind in {"logic", "partial_logic"}:
        if kind == "partial_logic": body = f"    if (!({ins[0]} && {ins[1]})) {out} = {ins[0]} ^ {ins[1]};"
        elif mutation == "omitted_condition": body = f"    {out} = {ins[0]};"
        elif p["rule"] == "xor3": body = f"    {out} = {ins[0]} ^ {ins[1]} ^ {ins[2]};"
        elif p["rule"] == "majority3": body = f"    {out} = ({ins[0]} && {ins[1]}) || ({ins[0]} && {ins[2]}) || ({ins[1]} && {ins[2]});"
        elif p["rule"] == "mode_override": body = f"    {out} = {ins[3]} || ({ins[0]} ? ({ins[1]} && {ins[2]}) : ({ins[1]} || {ins[2]}));"
        else: body = f"    {out} = {ins[0]} && ({ins[1]} || !{ins[2]}) && ({ins[3]} || {ins[4]});"
    else:
        # Static zero is intentionally arbitrary: pre-synchronization outputs are not checked.
        width = _output_width(case)
        if kind == "down": body = f"    static ap_uint<{width}> s = 0; if ({ins[0]}) s = {p['reset_value']}; else if (s == {p['low']}) s = {p['high']}; else s = s - 1; {out} = s;"
        elif kind == "saturating": body = f"    static ap_uint<{width}> s = 0; if ({ins[0]}) s = {p['reset_value']}; else if ({ins[1]} && s < {p['high']}) s = s + 1; {out} = s;"
        elif kind == "load": body = f"    static ap_uint<{width}> s = 0; if ({ins[0]}) s = {p['reset_value']}; else if ({ins[1]}) s = {ins[2]}; else if ({ins[3]}) {{ if (s == {p['high']}) s = {p['low']}; else s = s + 1; }} {out} = s;"
        else:
            reset_name, enable_name = p["reset_name"], p["enable_name"]
            reset_on = f"({reset_name} == {p['reset_active']})"
            if mutation == "resetpolarity": reset_on = f"(!{reset_on})"
            enable_on = f"({enable_name} == {p['enable_active']})"; upper = p['high'] + (1 if mutation == "wrongwrap" else 0)
            update = f"if (s == {upper}) s = {p['low']}; else s = s + 1;"
            if p["priority"] == "reset_first": conditional = f"if {reset_on} s = {p['reset_value']}; else if {enable_on} {{ {update} }}"
            else: conditional = f"if {enable_on} {{ {update} }} else if {reset_on} s = {p['reset_value']};"
            body = f"    static ap_uint<{width}> s = 0; {conditional} {out} = s;"
    return "#include <ap_int.h>\n\n" + sig + " {\n" + body + "\n}\n"


def reference_testbench(case: dict[str, object], mutation: str | None = None) -> str | None:
    if cpp_source(case, mutation) is None: return None
    p = case["parameters"]; ports = p["ports"]; inputs = _inputs(case); outputs = [x for x in ports if x["direction"] == "out"]
    lines = ["#include <ap_int.h>", "#include <cstdio>", _prototype(ports), "int main() {"]
    state = None
    for row, values in enumerate(probes(case), 1):
        expected, state = step(case, values, state, mutation)
        lines.append("    {")
        lines += [f"        {_ctype(port)} in_{port['name']} = {value};" for port, value in zip(inputs, values)]
        lines += [f"        {_ctype(port)} out_{port['name']} = 0;" for port in outputs]
        args = ["in_" + x["name"] for x in inputs] + ["out_" + x["name"] for x in outputs]
        lines.append("        TopModule(" + ", ".join(args) + ");")
        for name, value in expected.items(): lines.append(f"        if (out_{name} != {value}) {{ std::fprintf(stderr, \"row {row}: {name}\\n\"); return 1; }}")
        lines.append("    }")
    lines += ["    return 0;", "}"]
    return "\n".join(lines) + "\n"
