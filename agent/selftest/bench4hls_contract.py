"""Composable scalar contracts, deterministic traces and bounded oracle comparison.

The public-to-contract translation remains untrusted. Neither typing, coverage
nor agreement between two translations is a certificate of public semantics.
"""
import copy
import itertools
import math
import random
import re

from .bench4hls_architecture import interface
from .bench4hls_primitives import Type, bounds, dependencies, evaluate, infer_type


VERSION = 'bench4hls-contract-0.1.0'
PREDICATE = Type(1, False)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _keys(value, fields, label):
    _require(isinstance(value, dict) and set(value) == set(fields),
             label + ' fields must be: ' + ', '.join(sorted(fields)))


def _strings(value, label, limit=32):
    _require(isinstance(value, list) and len(value) <= limit and
             all(isinstance(v, str) and 0 < len(v.strip()) <= 2000 for v in value),
             'Invalid ' + label)


def public_segments(problem):
    """Stable IDs bind to exact public text spans, including non-ASCII text."""
    _require(isinstance(problem, str) and 0 < len(problem) <= 200_000,
             'Invalid public problem text')
    result = []
    for match in re.finditer(r'[^\r\n]+', problem):
        raw = match.group()
        if not raw.strip():
            continue
        start = match.start() + len(raw) - len(raw.lstrip())
        end = match.end() - len(raw) + len(raw.rstrip())
        result.append(dict(id=f'P{len(result):03d}', text=problem[start:end],
                           start=start, end=end))
    _require(len(result) <= 1024, 'Too many public segments')
    return result


def _choose(condition, yes, no):
    if condition is None:
        return yes if yes == no else None
    return yes if condition else no


class CompiledContract:
    def __init__(self, raw, signature, types):
        self.raw = copy.deepcopy(raw)
        self.signature = signature
        self.types = types
        self.nodes = self.raw['nodes']
        self.states = self.raw['state']
        self.outputs = self.raw['outputs']
        self.node_by_id = {n['id']: n for n in self.nodes}

    def initial_state(self):
        return {s['name']: None for s in self.states}

    def input_dependencies(self, ref):
        result, visited, pending = set(), set(), [ref]
        while pending:
            item = pending.pop()
            if item in visited:
                continue
            visited.add(item)
            if item.startswith('i') and item in self.types:
                result.add(item)
            elif item in self.node_by_id:
                pending.extend(dependencies(self.node_by_id[item]))
        return result

    def ancestors(self, ref):
        result = set()
        pending = [ref]
        while pending:
            item = pending.pop()
            if item in result or item not in self.node_by_id:
                continue
            result.add(item)
            pending.extend(dependencies(self.node_by_id[item]))
        return result

    def _environment(self, inputs, state):
        env = {'true': 1, 'false': 0}
        env.update({p['alias']: v for p, v in zip(self.signature['inputs'], inputs)})
        env.update(state)
        for node in self.nodes:
            env[node['id']] = evaluate(node, env, self.types)
        return env

    def step(self, inputs, state):
        _require(isinstance(inputs, list) and len(inputs) == len(self.signature['inputs']),
                 'Input arity mismatch')
        for value, param in zip(inputs, self.signature['inputs']):
            lo, hi = bounds(self.types[param['alias']])
            _require(type(value) is int and lo <= value <= hi, 'Input outside public type')
        _require(isinstance(state, dict) and set(state) == set(self.initial_state()),
                 'State names differ from contract')
        for name, value in state.items():
            lo, hi = bounds(self.types[name])
            _require(value is None or type(value) is int and lo <= value <= hi,
                     'Invalid reference state value')
        old = self._environment(inputs, state)
        updated, events = {}, {'states': {}, 'outputs': {}, 'masks': {}}
        for item in self.states:
            name = item['name']
            enabled, update = old[item['enable']], old[item['update']]
            reset = item['reset']
            reset_on = old[reset['condition']] if reset else 0
            if reset is None:
                value = _choose(enabled, update, state[name])
                branch = 'update' if enabled == 1 else 'hold' if enabled == 0 else 'unknown'
            elif reset['priority'] == 'reset_first':
                value = _choose(reset_on, reset['value'], _choose(enabled, update, state[name]))
                branch = ('reset' if reset_on == 1 else
                          ('update' if enabled == 1 else 'hold' if enabled == 0 else 'unknown')
                          if reset_on == 0 else 'unknown')
            else:
                value = _choose(enabled, update, _choose(reset_on, reset['value'], state[name]))
                branch = ('update' if enabled == 1 else
                          ('reset' if reset_on == 1 else 'hold' if reset_on == 0 else 'unknown')
                          if enabled == 0 else 'unknown')
            updated[name] = value
            events['states'][name] = dict(branch=branch, before=state[name], after=value,
                                          enabled=enabled, reset=reset_on)
        after = self._environment(inputs, updated) if self.states else old
        expected = {}
        for output in self.outputs:
            env = old if output['observe'] == 'before' else after
            when, value = env[output['when']], env[output['value']]
            reason = ('unknown_guard' if when is None else 'disabled' if not when else
                      'unknown_value' if value is None else 'checked')
            events['outputs'][output['name']] = reason
            if reason == 'checked':
                expected[output['name']] = value
        for node in self.nodes:
            if node['op'] == 'masked_write':
                events['masks'][node['id']] = old[node['mask']]
        return expected, updated, events


def compile_contract(raw, problem, top):
    signature = interface(problem, top)
    segments = public_segments(problem)
    _keys(raw, ('schema_version', 'decision', 'nodes', 'state', 'outputs', 'excluded', 'reasons'),
          'Contract')
    _require(type(raw['schema_version']) is int and raw['schema_version'] == 1,
             'Unsupported contract schema')
    for field, limit in (('nodes', 128), ('state', 8), ('outputs', 8)):
        _require(isinstance(raw[field], list) and len(raw[field]) <= limit, 'Invalid ' + field)
    _strings(raw['excluded'], 'exclusions')
    _strings(raw['reasons'], 'reasons')
    if raw['decision'] == 'abstain':
        _require(raw['reasons'] and not any(raw[n] for n in ('nodes', 'state', 'outputs')),
                 'Abstention requires reasons and no executable contract')
        return None
    _require(raw['decision'] in ('ready', 'partial') and not raw['reasons'], 'Invalid decision')
    _require(bool(raw['excluded']) == (raw['decision'] == 'partial'),
             'Partial scope requires explicit exclusions')
    evidence_ids = {s['id'] for s in segments}

    def evidence(item):
        refs = item['evidence']
        _require(isinstance(refs, list) and 1 <= len(refs) <= len(evidence_ids) and
                 all(isinstance(v, str) and v in evidence_ids for v in refs) and
                 len(refs) == len(set(refs)), 'Evidence must contain unique public segment IDs')

    types = {'true': PREDICATE, 'false': PREDICATE}
    types.update({p['alias']: Type(p['width'], p['signed']) for p in signature['inputs']})
    for index, state in enumerate(raw['state']):
        _keys(state, ('name', 'width', 'signed', 'update', 'enable', 'reset', 'evidence'), 'State')
        _require(state['name'] == 's' + str(index), 'States must be s0,s1,...')
        types[state['name']] = Type(state['width'], state['signed'])
        evidence(state)
    for index, node in enumerate(raw['nodes']):
        _require(isinstance(node, dict) and node.get('id') == 'v' + str(index),
                 'Nodes must be v0,v1,... in dependency order')
        types[node['id']] = infer_type(node, types)

    def reference(ref, expected_type=None):
        _require(isinstance(ref, str) and ref in types, 'Unknown reference: ' + str(ref))
        _require(expected_type is None or types[ref] == expected_type,
                 'Reference type mismatch: ' + ref)

    for state in raw['state']:
        reference(state['update'], types[state['name']])
        reference(state['enable'], PREDICATE)
        reset = state['reset']
        if reset is not None:
            _keys(reset, ('condition', 'value', 'priority'), 'Reset')
            reference(reset['condition'], PREDICATE)
            lo, hi = bounds(types[state['name']])
            _require(type(reset['value']) is int and lo <= reset['value'] <= hi,
                     'Reset value outside state type')
            _require(reset['priority'] in ('reset_first', 'enable_first'), 'Invalid reset priority')
    public_outputs = {p['name']: Type(p['width'], p['signed']) for p in signature['outputs']}
    seen = set()
    for output in raw['outputs']:
        _keys(output, ('name', 'value', 'observe', 'when', 'evidence'), 'Output')
        _require(isinstance(output['name'], str) and output['name'] in public_outputs and
                 output['name'] not in seen, 'Invalid or duplicate output')
        seen.add(output['name'])
        reference(output['value'], public_outputs[output['name']])
        reference(output['when'], PREDICATE)
        _require(output['observe'] in ('before', 'after'), 'Output observation phase required')
        evidence(output)
    _require(seen and (seen == set(public_outputs) or raw['decision'] == 'partial'),
             'Missing output requires partial scope')
    return CompiledContract(raw, signature, types)


def _input_pool(contract, seed):
    params = contract.signature['inputs']
    domains = [bounds(contract.types[p['alias']]) for p in params]
    space = math.prod(hi - lo + 1 for lo, hi in domains)
    if space <= 256:
        return [list(v) for v in itertools.product(*(range(lo, hi + 1) for lo, hi in domains))], space
    rows, seen = [], set()

    def add(row):
        item = tuple(row)
        if item not in seen:
            seen.add(item)
            rows.append(list(row))

    zero = [0] * len(params)
    for select in range(4):
        add([(0, lo, hi, min(1, hi))[select] for lo, hi in domains])
    control_refs = set()
    for state in contract.states:
        control_refs.update(contract.input_dependencies(state['enable']))
        if state['reset']:
            control_refs.update(contract.input_dependencies(state['reset']['condition']))
    for node in contract.nodes:
        if node['op'] == 'masked_write':
            control_refs.update(contract.input_dependencies(node['mask']))
    controls = [i for i, p in enumerate(params) if p['alias'] in control_refs]
    control_size = math.prod(domains[i][1] - domains[i][0] + 1 for i in controls)
    if control_size <= 256:
        for values in itertools.product(*(range(domains[i][0], domains[i][1] + 1) for i in controls)):
            for base in (zero, [hi for lo, hi in domains]):
                row = list(base)
                for i, value in zip(controls, values):
                    row[i] = value
                add(row)
    for i, (lo, hi) in enumerate(domains):
        values = [lo, hi, 0, min(1, hi), lo + 1, hi - 1]
        width = params[i]['width']
        for bit in sorted({0, width // 2, width - 1}):
            value = 1 << bit
            if value <= hi:
                values.append(value)
        for value in values:
            if lo <= value <= hi:
                row = list(zero)
                row[i] = value
                add(row)
    rng = random.Random(seed)
    for _ in range(64):
        add([rng.randint(lo, hi) for lo, hi in domains])
    return rows, space


def _schedule(contract, max_calls, seed, *, cover_counter_wrap=False):
    _require(type(max_calls) is int and 1 <= max_calls <= 1024, 'Call budget must be 1..1024')
    _require(type(seed) is int, 'Seed must be integer')
    pool, space = _input_pool(contract, seed)
    directed = []
    planning_state = {s['name']: s['reset']['value'] if s['reset'] else None for s in contract.states}
    observations = [(row, contract.step(row, planning_state)) for row in pool] if contract.states else []

    def pick(predicate):
        return next((list(row) for row, (_, new, events) in observations if predicate(new, events)), None)

    def append(*rows):
        for row in rows:
            if row is not None and len(directed) < max_calls:
                directed.append(list(row))

    if cover_counter_wrap:
        for counter in getattr(contract, 'binding_counter_ranges', []):
            name = counter['state']
            reset_row = pick(lambda new, e: e['states'][name]['branch'] == 'reset')
            update_row = pick(lambda new, e: e['states'][name]['branch'] == 'update')
            if reset_row is not None and update_row is not None:
                append(reset_row)
                period = counter['upper'] - counter['lower'] + 1
                for _ in range(min(period, max_calls - len(directed))):
                    append(update_row)

    for state in contract.states:
        name = state['name']
        reset = pick(lambda new, e: e['states'][name]['branch'] == 'reset')
        update = pick(lambda new, e: e['states'][name]['branch'] == 'update' and
                      new[name] is not None and new[name] != planning_state[name])
        if update is None:
            update = pick(lambda new, e: e['states'][name]['branch'] == 'update')
        hold = pick(lambda new, e: e['states'][name]['branch'] == 'hold')
        append(reset, update, hold, reset, hold, update)
        for ref in sorted(contract.ancestors(state['update'])):
            node = contract.node_by_id[ref]
            if node['op'] == 'masked_write':
                lanes = contract.types[node['mask']].width
                full = pick(lambda new, e: e['states'][name]['branch'] == 'update' and
                            e['masks'].get(ref) == (1 << lanes) - 1 and
                            new[name] is not None and new[name] != planning_state[name])
                for lane in range(lanes):
                    if len(directed) >= max_calls:
                        break
                    part = pick(lambda new, e: e['states'][name]['branch'] == 'update' and
                                e['masks'].get(ref) == 1 << lane)
                    append(reset, full, reset, part, hold)
        if any(contract.node_by_id[n]['op'] in ('add', 'sub')
               for n in contract.ancestors(state['update'])):
            append(reset)
            for _ in range(min((1 << min(state['width'], 6)) + 1, 32)):
                append(update)
    return (directed + pool)[:max_calls], space


def make_vectors(contract, max_calls=256, seed=20260925, *, cover_counter_wrap=False):
    stimuli, space = _schedule(contract, max_calls, seed, cover_counter_wrap=cover_counter_wrap)
    required = {'output.' + o['name'] for o in contract.outputs}
    for state in contract.states:
        name = state['name']
        required.add(name + '.update')
        if state['enable'] != 'true':
            required.add(name + '.hold')
        if state['reset']:
            required.update((name + '.reset', name + '.reset_from_nonreset', name + '.post_reset_update'))
        for ref in sorted(contract.ancestors(state['update'])):
            node = contract.node_by_id[ref]
            if node['op'] == 'masked_write':
                required.update(f'{name}.{ref}.lane.{i}' for i in range(contract.types[node['mask']].width))
    counters = {item['state']: item for item in getattr(contract, 'binding_counter_ranges', [])} if cover_counter_wrap else {}
    required.update(name + '.wrap' for name in counters)
    witnesses = {}
    counts = {o['name']: 0 for o in contract.outputs}
    skipped = {o['name']: 0 for o in contract.outputs}
    state = contract.initial_state()
    after_reset = {s['name']: False for s in contract.states}
    vectors = []
    for index, inputs in enumerate(stimuli):
        expected, new, events = contract.step(inputs, state)
        vectors.append(dict(inputs=inputs, expected=expected))
        for output in contract.outputs:
            name = output['name']
            if name in expected:
                counts[name] += 1
                witnesses.setdefault('output.' + name, index)
            else:
                skipped[name] += 1
        for item in contract.states:
            name = item['name']
            event = events['states'][name]
            counter = counters.get(name)
            if counter and event['branch'] == 'update' and event['before'] == counter['upper'] and event['after'] == counter['lower']:
                witnesses.setdefault(name + '.wrap', index)
            witnesses.setdefault(name + '.' + event['branch'], index)
            if event['branch'] == 'reset':
                after_reset[name] = True
                if state[name] is not None and state[name] != item['reset']['value']:
                    witnesses.setdefault(name + '.reset_from_nonreset', index)
            elif event['branch'] == 'update':
                if after_reset[name]:
                    witnesses.setdefault(name + '.post_reset_update', index)
                after_reset[name] = False
                for ref in sorted(contract.ancestors(item['update'])):
                    if ref in events['masks']:
                        mask = events['masks'][ref]
                        if mask and mask & (mask - 1) == 0:
                            witnesses.setdefault(f'{name}.{ref}.lane.{mask.bit_length()-1}', index)
        state = new
    coverage = dict(calls=len(vectors), assertions=sum(counts.values()), per_output=counts,
                    skipped=skipped, seed=seed, max_calls=max_calls,
                    initial_state='unknown_until_synchronized', stage='planned_vectors',
                    semantic_correctness='unverified', stateful=bool(contract.states),
                    counter_wrap_requested=cover_counter_wrap,
                    exhaustive_input_domain=not contract.states and len({tuple(v['inputs']) for v in vectors}) == space,
                    obligations=[dict(id=name, status='covered' if name in witnesses else 'missing',
                                      first_call=witnesses.get(name)) for name in sorted(required)],
                    missing=sorted(required - witnesses.keys()))
    return vectors, coverage


def compare_contracts(left, right, max_calls=256, seed=20260925, *, cover_counter_wrap=False):
    _require(left.signature == right.signature, 'Cannot compare different interfaces')
    public = {p['name'] for p in left.signature['outputs']}
    left_outputs, right_outputs = ({o['name'] for o in c.outputs} for c in (left, right))
    result = dict(status='inconclusive', semantic_correctness='unverified',
                  automatic_acceptance_allowed=False, repair_feedback_allowed=False,
                  comparisons=0, unknown_gaps=0, comparison_calls=0)
    if left_outputs != right_outputs:
        return dict(result, status='conflict', counterexample=dict(kind='output_scope_disagreement',
                    left=sorted(left_outputs), right=sorted(right_outputs)))
    first, space = _schedule(left, max_calls, seed, cover_counter_wrap=cover_counter_wrap)
    second, _ = _schedule(right, max_calls, seed, cover_counter_wrap=cover_counter_wrap)
    if not left.states and not right.states:
        stimuli = [list(v) for v in dict.fromkeys(tuple(v) for v in first + second)]
    else:
        stimuli = first + second
    left_state, right_state = left.initial_state(), right.initial_state()
    checked = set()
    prefix = []
    for index, inputs in enumerate(stimuli):
        prefix.append(inputs)
        a, left_state, ea = left.step(inputs, left_state)
        b, right_state, eb = right.step(inputs, right_state)
        result['comparison_calls'] += 1
        for name in sorted(left_outputs):
            if name in a and name in b:
                result['comparisons'] += 1
                checked.add(name)
                if a[name] != b[name]:
                    return dict(result, status='conflict', counterexample=dict(
                        kind='value_disagreement', call=index, signal=name,
                        inputs=inputs, left=a[name], right=b[name], prefix=copy.deepcopy(prefix)))
            elif (name in a) != (name in b):
                reasons = (ea['outputs'][name], eb['outputs'][name])
                if not left.states and not right.states or 'disabled' in reasons:
                    return dict(result, status='conflict', counterexample=dict(
                        kind='defined_domain_disagreement', call=index, signal=name,
                        inputs=inputs, left=a.get(name), right=b.get(name), prefix=copy.deepcopy(prefix)))
                result['unknown_gaps'] += 1
    full_domain = not left.states and not right.states and len(stimuli) == space
    result['domain_complete'] = full_domain
    result['coverage_scope'] = 'partial' if public != left_outputs or left.raw['excluded'] or right.raw['excluded'] else 'ready'
    if checked != left_outputs or result['unknown_gaps']:
        result['reason'] = 'Insufficient mutually known observations'
    else:
        result['status'] = 'consistent_on_declared_domain' if full_domain else 'consistent_on_probes'
    return result
