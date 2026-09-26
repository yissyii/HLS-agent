"""Closed parameter-binding frontend lowered to the Bench4HLS graph contract."""
import copy

from .bench4hls_architecture import interface
from .bench4hls_contract import PREDICATE, compile_contract, public_segments
from .bench4hls_primitives import Type, bounds
from .spec import keys, require


def _evidence(value, ids):
    require(isinstance(value, list) and 1 <= len(value) <= len(ids) and
            all(isinstance(item, str) and item in ids for item in value) and
            len(value) == len(set(value)), 'Evidence must contain unique public segment IDs')


def _integer(value, low, high, label):
    require(type(value) is int and low <= value <= high, 'Invalid ' + label)
    return value


def compile_bindings(raw, problem, top, *, extensions=False):
    """Validate a v1 binding specification and deterministically lower it to a graph."""
    require(type(extensions) is bool, 'extensions must be a bool')
    signature = interface(problem, top)
    segment_ids = {item['id'] for item in public_segments(problem)}
    keys(raw, {'schema_version', 'decision', 'components', 'outputs', 'excluded', 'reasons'},
         'Binding contract')
    require(type(raw['schema_version']) is int and raw['schema_version'] == 1,
            'Unsupported binding schema')
    for field, limit in (('components', 32), ('outputs', 8), ('excluded', 32), ('reasons', 32)):
        require(isinstance(raw[field], list) and len(raw[field]) <= limit, 'Invalid ' + field)
    for field in ('excluded', 'reasons'):
        require(all(isinstance(item, str) and 0 < len(item.strip()) <= 2000 for item in raw[field]),
                'Invalid ' + field)
    if raw['decision'] == 'abstain':
        require(raw['reasons'] and not raw['components'] and not raw['outputs'],
                'Abstention requires reasons and no executable binding')
        return None
    require(raw['decision'] in {'ready', 'partial'} and not raw['reasons'], 'Invalid decision')
    require(bool(raw['excluded']) == (raw['decision'] == 'partial'),
            'Partial scope requires explicit exclusions')

    input_types = {item['alias']: Type(item['width'], item['signed']) for item in signature['inputs']}
    public_inputs = set(input_types)
    component_types, component_refs, component_deps, component_evidence = {}, {}, {}, {}
    states, nodes, counter_ranges = [], [], []

    def lowered_ref(ref):
        return component_refs.get(ref, ref)

    def node(op, **fields):
        require(len(nodes) < 128, 'Lowered graph exceeds 128 nodes')
        for name in ('arg', 'a', 'b', 'cond', 'yes', 'no', 'old', 'data', 'mask'):
            if name in fields:
                fields[name] = lowered_ref(fields[name])
        if 'args' in fields:
            fields['args'] = [lowered_ref(ref) for ref in fields['args']]
        result = dict(id='v' + str(len(nodes)), op=op, **fields)
        nodes.append(result)
        return result['id']

    def resolved(ref, allowed, label):
        require(isinstance(ref, str) and ref in allowed, 'Invalid ' + label + ' reference')
        return component_types[ref] if ref in component_types else input_types[ref]

    def control_predicate(item, allowed, label):
        require(isinstance(item, dict), 'Invalid ' + label)
        keys(item, {'input', 'active'}, label)
        source_type = resolved(item['input'], allowed, label + ' input')
        require(source_type == PREDICATE and (extensions or item['input'] in public_inputs),
                label + ' requires an unsigned one-bit input')
        _integer(item['active'], 0, 1, label + ' active')
        return lowered_ref(item['input']) if item['active'] else node('not', arg=item['input'])

    def enable(value, allowed):
        require(value is None or isinstance(value, dict), 'Invalid enable')
        return 'true' if value is None else control_predicate(value, allowed, 'Enable')

    def reset(value, state_type, allowed):
        require(value is None or isinstance(value, dict), 'Invalid reset')
        if value is None:
            return None
        keys(value, {'input', 'active', 'value', 'priority'}, 'Reset')
        condition = control_predicate({'input': value['input'], 'active': value['active']},
                                      allowed, 'Reset')
        lo, hi = bounds(state_type)
        _integer(value['value'], lo, hi, 'reset value')
        require(value['priority'] in {'reset_first', 'enable_first'}, 'Invalid reset priority')
        return dict(condition=condition, value=value['value'], priority=value['priority'])

    def concat_tree(refs):
        current = list(refs)
        while len(current) > 1:
            current = [group[0] if len(group) == 1 else node('concat', args=group)
                       for group in (current[index:index + 16]
                                     for index in range(0, len(current), 16))]
        return current[0]

    for index, component in enumerate(raw['components']):
        require(isinstance(component, dict) and component.get('id') == 'c' + str(index),
                'Components must be c0,c1,...')
        require(isinstance(component.get('kind'), str), 'Component kind required')
        kind = component['kind']
        allowed = public_inputs | set(component_types)
        expected = {
            'reverse_units': {'id', 'kind', 'input', 'unit_width', 'evidence'},
            'truth_table': {'id', 'kind', 'inputs', 'ones', 'zeros', 'dont_care', 'evidence'},
            'register': {'id', 'kind', 'data', 'enable', 'reset', 'mask', 'evidence'},
            'counter': {'id', 'kind', 'width', 'signed', 'lower', 'upper', 'enable', 'reset', 'evidence'},
        }
        if extensions:
            expected.update({
                'const': {'id', 'kind', 'width', 'signed', 'value', 'evidence'},
                'cast': {'id', 'kind', 'input', 'width', 'signed', 'evidence'},
                'slice': {'id', 'kind', 'input', 'lsb', 'width', 'evidence'},
                'concat': {'id', 'kind', 'inputs', 'evidence'},
                'binary': {'id', 'kind', 'operation', 'a', 'b', 'evidence'},
                'select': {'id', 'kind', 'cond', 'yes', 'no', 'evidence'},
                'shift': {'id', 'kind', 'input', 'direction', 'amount', 'evidence'},
            })
        require(kind in expected, 'Unsupported component kind')
        keys(component, expected[kind], 'Component')
        _evidence(component['evidence'], segment_ids)
        component_evidence[component['id']] = component['evidence']

        if kind == 'reverse_units':
            source_type = resolved(component['input'], allowed, 'reverse_units input')
            require(not source_type.signed, 'reverse_units input must be unsigned')
            unit = _integer(component['unit_width'], 1, source_type.width, 'unit_width')
            require(source_type.width % unit == 0, 'unit_width must divide input width')
            units = source_type.width // unit
            require(units <= 64 or unit == 1, 'reverse_units supports at most 64 units')
            if unit == 1:
                result = node('reverse', arg=component['input'])
            elif units == 1:
                result = lowered_ref(component['input'])
            else:
                refs = [node('slice', arg=component['input'], lsb=offset * unit, width=unit)
                        for offset in range(units)]
                result = concat_tree(refs)
            component_types[component['id']] = source_type
            component_refs[component['id']] = result
            component_deps[component['id']] = {component['input']}
        elif kind == 'const':
            state_type = Type(component['width'], component['signed'])
            lo, hi = bounds(state_type)
            _integer(component['value'], lo, hi, 'const value')
            component_types[component['id']] = state_type
            component_refs[component['id']] = node('const', width=state_type.width,
                                                    signed=state_type.signed, value=component['value'])
            component_deps[component['id']] = set()
        elif kind == 'cast':
            resolved(component['input'], allowed, 'cast input')
            state_type = Type(component['width'], component['signed'])
            component_types[component['id']] = state_type
            component_refs[component['id']] = node('cast', arg=component['input'], width=state_type.width,
                                                    signed=state_type.signed)
            component_deps[component['id']] = {component['input']}
        elif kind == 'slice':
            source_type = resolved(component['input'], allowed, 'slice input')
            width = _integer(component['width'], 1, source_type.width, 'slice width')
            lsb = _integer(component['lsb'], 0, source_type.width - width, 'slice lsb')
            state_type = Type(width, False)
            component_types[component['id']] = state_type
            component_refs[component['id']] = node('slice', arg=component['input'], lsb=lsb, width=width)
            component_deps[component['id']] = {component['input']}
        elif kind == 'concat':
            refs = component['inputs']
            require(isinstance(refs, list) and 2 <= len(refs) <= 16, 'concat requires 2..16 inputs')
            parts = [resolved(ref, allowed, 'concat input') for ref in refs]
            width = sum(part.width for part in parts)
            require(width <= 1024, 'concat width exceeds 1024')
            state_type = Type(width, False)
            component_types[component['id']] = state_type
            component_refs[component['id']] = node('concat', args=refs)
            component_deps[component['id']] = set(refs)
        elif kind == 'binary':
            a_type = resolved(component['a'], allowed, 'binary a')
            b_type = resolved(component['b'], allowed, 'binary b')
            require(a_type == b_type, 'binary inputs must have identical types')
            operation = component['operation']
            require(operation in {'add', 'sub', 'and', 'or', 'xor', 'eq', 'lt'}, 'Invalid binary operation')
            component_types[component['id']] = PREDICATE if operation in {'eq', 'lt'} else a_type
            component_refs[component['id']] = node(operation, a=component['a'], b=component['b'])
            component_deps[component['id']] = {component['a'], component['b']}
        elif kind == 'select':
            require(resolved(component['cond'], allowed, 'select cond') == PREDICATE,
                    'select condition must be unsigned one-bit')
            yes_type = resolved(component['yes'], allowed, 'select yes')
            require(yes_type == resolved(component['no'], allowed, 'select no'),
                    'select branches must have identical types')
            component_types[component['id']] = yes_type
            component_refs[component['id']] = node('select', cond=component['cond'],
                                                    yes=component['yes'], no=component['no'])
            component_deps[component['id']] = {component['cond'], component['yes'], component['no']}
        elif kind == 'shift':
            state_type = resolved(component['input'], allowed, 'shift input')
            direction = component['direction']
            require(direction in {'left', 'logical_right', 'arithmetic_right'}, 'Invalid shift direction')
            amount = _integer(component['amount'], 0, state_type.width, 'shift amount')
            op = {'left': 'shl', 'logical_right': 'lshr', 'arithmetic_right': 'ashr'}[direction]
            component_types[component['id']] = state_type
            component_refs[component['id']] = node(op, arg=component['input'], amount=amount)
            component_deps[component['id']] = {component['input']}
        elif kind == 'truth_table':
            refs = component['inputs']
            require(isinstance(refs, list) and 1 <= len(refs) <= 8 and len(refs) == len(set(refs)),
                    'truth_table requires 1..8 distinct inputs')
            for ref in refs:
                require(resolved(ref, allowed, 'truth_table input') == PREDICATE,
                        'truth_table inputs must be unsigned one-bit')
            arg = refs[0] if len(refs) == 1 else node('concat', args=refs)
            result = node('table', arg=arg, ones=component['ones'], zeros=component['zeros'],
                          dont_care=component['dont_care'])
            component_types[component['id']] = PREDICATE
            component_refs[component['id']] = result
            component_deps[component['id']] = set(refs)
        elif kind == 'register':
            state_type = resolved(component['data'], allowed, 'register data')
            enabled = enable(component['enable'], allowed)
            state_name = 's' + str(len(states))
            update = lowered_ref(component['data'])
            deps = {component['data']}
            mask = component['mask']
            if mask is not None:
                keys(mask, {'input', 'lane_width'}, 'Mask')
                lane = _integer(mask['lane_width'], 1, state_type.width, 'lane_width')
                require(state_type.width % lane == 0, 'lane_width must divide data width')
                require(resolved(mask['input'], allowed, 'mask input') == Type(state_type.width // lane, False) and
                        (extensions or mask['input'] in public_inputs), 'Mask requires an unsigned lane mask')
                update = node('masked_write', old=state_name, data=component['data'],
                              mask=mask['input'], lane_width=lane)
                deps.add(mask['input'])
            if component['enable'] is not None:
                deps.add(component['enable']['input'])
            if component['reset'] is not None:
                deps.add(component['reset']['input'])
            states.append(dict(name=state_name, width=state_type.width, signed=state_type.signed,
                               update=update, enable=enabled, reset=reset(component['reset'], state_type, allowed),
                               evidence=component['evidence']))
            component_types[component['id']] = state_type
            component_refs[component['id']] = state_name
            component_deps[component['id']] = deps
        else:
            state_type = Type(component['width'], component['signed'])
            lo, hi = bounds(state_type)
            _integer(component['lower'], lo, hi, 'counter lower')
            _integer(component['upper'], lo, hi, 'counter upper')
            require(component['lower'] < component['upper'], 'Counter lower must be less than upper')
            require(not state_type.signed or hi >= 1, 'Signed width-one counter cannot represent typed one')
            enabled = enable(component['enable'], allowed)
            state_name = 's' + str(len(states))
            lower = node('const', width=state_type.width, signed=state_type.signed, value=component['lower'])
            upper = node('const', width=state_type.width, signed=state_type.signed, value=component['upper'])
            one = node('const', width=state_type.width, signed=state_type.signed, value=1)
            at_upper = node('eq', a=state_name, b=upper)
            incremented = node('add', a=state_name, b=one)
            update = node('select', cond=at_upper, yes=lower, no=incremented)
            deps = set()
            if component['enable'] is not None:
                deps.add(component['enable']['input'])
            if component['reset'] is not None:
                deps.add(component['reset']['input'])
            reset_value = reset(component['reset'], state_type, allowed)
            if reset_value is not None:
                require(component['lower'] <= reset_value['value'] <= component['upper'],
                        'Counter reset must lie within counter range')
            states.append(dict(name=state_name, width=state_type.width, signed=state_type.signed,
                               update=update, enable=enabled, reset=reset_value,
                               evidence=component['evidence']))
            component_types[component['id']] = state_type
            component_refs[component['id']] = state_name
            component_deps[component['id']] = deps
            counter_ranges.append(dict(state=state_name, lower=component['lower'], upper=component['upper']))

    lowered_outputs, output_seen, reachable, pending = [], set(), set(), []
    public_outputs = {item['name']: Type(item['width'], item['signed']) for item in signature['outputs']}
    for output in raw['outputs']:
        keys(output, {'name', 'value', 'observe', 'evidence'}, 'Output')
        require(output['name'] in public_outputs and output['name'] not in output_seen,
                'Invalid or duplicate output')
        value_type = resolved(output['value'], public_inputs | set(component_types), 'output value')
        require(value_type == public_outputs[output['name']], 'Output type mismatch')
        require(output['observe'] in {'before', 'after'}, 'Output observation phase required')
        _evidence(output['evidence'], segment_ids)
        output_seen.add(output['name'])
        lowered_outputs.append(dict(name=output['name'], value=component_refs.get(output['value'], output['value']),
                                    observe=output['observe'], when='true', evidence=output['evidence']))
        if output['value'] in component_types:
            pending.append(output['value'])
    require(output_seen and (output_seen == set(public_outputs) or raw['decision'] == 'partial'),
            'Missing output requires partial scope')
    while pending:
        item = pending.pop()
        if item in reachable:
            continue
        reachable.add(item)
        pending.extend(ref for ref in component_deps[item] if ref in component_types)
    require(reachable == set(component_types), 'Every component must be reachable from an output')
    used_inputs = {output['value'] for output in raw['outputs'] if output['value'] in public_inputs}
    for item in reachable:
        used_inputs.update(ref for ref in component_deps[item] if ref in public_inputs)
    unused = tuple(sorted(public_inputs - used_inputs, key=lambda ref: int(ref[1:])))
    require(raw['decision'] != 'ready' or not unused, 'Ready binding omits public inputs')
    lowered = dict(schema_version=1, decision=raw['decision'], nodes=nodes, state=states,
                   outputs=lowered_outputs, excluded=copy.deepcopy(raw['excluded']), reasons=[])
    contract = compile_contract(lowered, problem, top)
    contract.binding_spec = copy.deepcopy(raw)
    contract.binding_unused_inputs = unused
    contract.binding_counter_ranges = counter_ranges
    return contract
