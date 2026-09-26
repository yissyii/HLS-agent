"""Output-total, source-bound parameter declarations for the experimental path."""
from __future__ import annotations

import copy
import re

from tools.selftest_task_corpus import semantic_slices
from .bench4hls_architecture import interface
from .bench4hls_bindings import compile_bindings
from .bench4hls_contract import public_segments
from .spec import keys, require


OUTPUT_FIELDS = {'value', 'observe', 'evidence', 'reason'}


def evidence_mapping(problem):
    lines = public_segments(problem)
    slices = semantic_slices(problem)
    mapping = {
        part['id']: [line['id'] for line in lines
                     if line['start'] < part['end'] and part['start'] < line['end']]
        for part in slices
    }
    require(all(mapping.values()), 'Semantic slice lacks source-line binding')
    return slices, mapping


class PublicDomainConflict(ValueError):
    category = 'public_domain_conflict'


def explicit_unconstrained_rows(problem, signature):
    """Read only a narrow, positive, literal public row-list syntax.

    No task IDs or expected Boolean values are used. Unsupported phrasings and
    ambiguous bit order yield no parsed rule, not a claim of a fully defined domain.
    """
    inputs = signature['inputs']
    if len(inputs) == 1 and not inputs[0]['signed'] and inputs[0]['width'] <= 8:
        order = [inputs[0]['name']]
        width = inputs[0]['width']
    elif 1 <= len(inputs) <= 8 and all(p['width'] == 1 and not p['signed'] for p in inputs):
        names = {p['name'] for p in inputs}
        orders = set()
        for match in re.finditer(r'\(([^()]*)\)', problem):
            parts = tuple(item.strip().strip('`').strip() for item in match.group(1).split(','))
            if len(parts) == len(names) and set(parts) == names:
                orders.add(parts)
        if len(orders) != 1:
            return []
        order = list(next(iter(orders)))
        for name, bit in ((order[0], 'MSB'), (order[-1], 'LSB')):
            if not re.search(r'\b' + re.escape(name) + r'\b`?\s+is\s+(?:the\s+)?' + bit + r'\b', problem, re.I):
                return []
        width = len(inputs)
    else:
        return []
    expression = re.compile(
        r'\bFor\s+(?:the\s+)?don[\x27\u2018\u2019]t[\s\u2010-\u2015-]+care'
        r'(?:\s+input)?\s+(?:conditions|rows|combinations)\s*'
        r'\((?P<rows>\s*\d+(?:\s*,\s*\d+)*\s*)\)(?P<tail>[^.\n\r]*)', re.I)
    constraints = []
    name_token = r'`?[A-Za-z_]\w*`?'
    names_clause = name_token + r'(?:\s*(?:,\s*(?:and\s+)?|\band\s+)' + name_token + r')*'
    # Accept an explicit positive predicate about exactly the listed outputs.
    # Later mentions, negated predicates and mixed clauses are not interpreted.
    permission = re.compile(r'^\s*,\s*(?P<names>' + names_clause + r')\s+'
        r'(?:may\s+(?:independently\s+)?assign\s+0\s+or\s+1|'
        r'(?:is|are)\s+(?:independently\s+)?(?:unconstrained|unspecified))\s*(?:;|$)', re.I)
    for match in expression.finditer(problem):
        tail = match.group('tail')
        freedom = permission.match(tail)
        mentioned = re.findall(name_token, freedom.group('names')) if freedom else []
        mentioned = [name.strip('`') for name in mentioned if name != 'and']
        output_names = {p['name'] for p in signature['outputs']}
        outputs = mentioned if set(mentioned) <= output_names else []
        rows = [int(value.strip()) for value in match.group('rows').split(',')]
        if not freedom or not outputs or len(set(rows)) != len(rows) or any(value >= 1 << width for value in rows):
            continue
        constraints.append(dict(kind='explicit_unconstrained_rows', input_order=order,
                                rows=rows, outputs=outputs, start=match.start(), end=match.end(),
                                text=match.group(), parser_scope='literal_positive_row_list_only'))
    return constraints


def verify_public_domains(contract, constraints):
    from .bench4hls_primitives import dependencies
    outputs = {o['name']: o for o in contract.outputs}
    states = set(contract.initial_state())
    for constraint in constraints:
        checked = set(constraint['outputs']) & set(outputs)
        for name in checked:
            ref = outputs[name]['value']
            references = {ref}
            for node_id in contract.ancestors(ref):
                references.update(dependencies(contract.node_by_id[node_id]))
            if references & states:
                raise PublicDomainConflict('Explicit unconstrained rows need a state-independent output domain: ' + name)
        for row in constraint['rows']:
            order = constraint['input_order']
            values = ({order[0]: row} if len(order) == 1 else
                      {name: (row >> (len(order) - index - 1)) & 1 for index, name in enumerate(order)})
            inputs = [values[p['name']] for p in contract.signature['inputs']]
            expected, _, _ = contract.step(inputs, contract.initial_state())
            forbidden = checked & set(expected)
            if forbidden:
                raise PublicDomainConflict('Public text leaves row ' + str(row) + ' unconstrained for ' +
                                           ', '.join(sorted(forbidden)) + '; do not emit a fixed expected value')


def obligation_payload(problem, signature):
    slices, mapping = evidence_mapping(problem)
    # Role labels help locate text; they never decide the behavioral truth.
    return dict(
        public_problem=problem, interface=signature, public_segments=slices,
        public_input_references={p['name']: 'in:' + p['name'] for p in signature['inputs']},
        public_domain_constraints=explicit_unconstrained_rows(problem, signature),
        output_template={p['name']: dict(value=None, observe=None, evidence=[], reason=None)
                         for p in signature['outputs']},
        evidence_policy='IDs bind source spans only; heuristic roles are not established semantics.',
        component_reference_policy='Program assigns c0,c1,... by array order. Do not emit component id fields.',
    )


def obligation_status(raw):
    resolved = [name for name, entry in raw['outputs'].items() if entry['value'] is not None]
    unresolved = [name for name, entry in raw['outputs'].items() if entry['value'] is None]
    reasons = [name + ': ' + raw['outputs'][name]['reason'] for name in unresolved]
    return dict(decision='ready' if not unresolved else 'partial' if resolved else 'abstain',
                excluded=reasons if resolved else [], reasons=reasons if not resolved else [],
                declared_outputs=resolved, unresolved_outputs=unresolved)


def compile_obligations(raw, problem, top):
    signature = interface(problem, top)
    slices, mapping = evidence_mapping(problem)
    keys(raw, {'schema_version', 'components', 'outputs'}, 'Output obligation declaration')
    require(type(raw['schema_version']) is int and raw['schema_version'] == 2, 'Expected schema_version 2')
    require(isinstance(raw['components'], list) and len(raw['components']) <= 32, 'Invalid components')
    names = {p['name'] for p in signature['outputs']}
    require(isinstance(raw['outputs'], dict) and set(raw['outputs']) == names,
            'Output map must contain every exact public output name: ' + ', '.join(sorted(names)))
    public_refs = {'in:' + p['name']: p['alias'] for p in signature['inputs']}

    def reference(value):
        require(isinstance(value, str), 'Reference must be a string')
        if value in public_refs:
            return public_refs[value]
        require(re.fullmatch(r'c(?:0|[1-9][0-9]*)', value) is not None,
                'Reference must be an exact public in:name or a preceding cN component')
        return value

    def evidence(ids, allow_empty=False):
        require(isinstance(ids, list) and (allow_empty or bool(ids))
                and all(isinstance(item, str) and item in mapping for item in ids)
                and len(ids) == len(set(ids)), 'Evidence must use unique supplied semantic slice IDs')
        return list(dict.fromkeys(line for sid in ids for line in mapping[sid]))

    def translate_component(value):
        require(isinstance(value, dict) and 'id' not in value, 'Component IDs are program assigned')
        result = copy.deepcopy(value)
        result['evidence'] = evidence(result.get('evidence'))
        # Translate only reference positions; constants/reset values remain numbers.
        for key in ('input', 'data', 'a', 'b', 'cond', 'yes', 'no'):
            if key in result:
                result[key] = reference(result[key])
        if 'inputs' in result:
            require(isinstance(result['inputs'], list), 'inputs must be a reference list')
            result['inputs'] = [reference(item) for item in result['inputs']]
        for key in ('enable', 'reset', 'mask'):
            if key in result and result[key] is not None:
                require(isinstance(result[key], dict), 'Invalid control object')
                result[key]['input'] = reference(result[key].get('input'))
        return result

    outputs = []
    for param in signature['outputs']:
        name = param['name']
        entry = raw['outputs'][name]
        keys(entry, OUTPUT_FIELDS, 'Output ' + name)
        if entry['value'] is None:
            require(entry['observe'] is None and isinstance(entry['reason'], str)
                    and 0 < len(entry['reason'].strip()) <= 2000,
                    'Unresolved output requires null value/observe and an explicit reason: ' + name)
            evidence(entry['evidence'], allow_empty=True)
        else:
            require(entry['reason'] is None, 'Checked output reason must be null: ' + name)
            require(entry['observe'] in ('before', 'after'), 'Observation phase required: ' + name)
            outputs.append(dict(name=name, value=reference(entry['value']), observe=entry['observe'],
                                evidence=evidence(entry['evidence'])))
    scope = obligation_status(raw)
    require(scope['decision'] != 'abstain' or not raw['components'],
            'Fully unresolved declarations must not contain unused components')
    components = []
    for index, component in enumerate(raw['components']):
        item = translate_component(component)
        item['id'] = 'c' + str(index)
        components.append(item)
    legacy = dict(schema_version=1, decision=scope['decision'], components=components,
                  outputs=outputs, excluded=scope['excluded'], reasons=scope['reasons'])
    contract = compile_bindings(legacy, problem, top, extensions=True)
    if contract is not None:
        verify_public_domains(contract, explicit_unconstrained_rows(problem, signature))
        contract.obligation_spec = copy.deepcopy(raw)
        contract.obligation_evidence_mapping = mapping
        contract.obligation_scope = scope
    return contract


def public_output_coverage(signature, contract, coverage, scope):
    declared = {item['name'] for item in contract.outputs} if contract is not None else set()
    counts = coverage.get('per_output', {}) if coverage else {}
    per_output = {param['name']: counts.get(param['name'], 0) for param in signature['outputs']}
    unresolved = [name for name in per_output if name not in declared]
    unchecked = [name for name, count in per_output.items() if not count]
    return dict(
        per_output=per_output, declared_outputs=sorted(declared), unresolved_outputs=unresolved,
        outputs_without_checks=unchecked,
        complete=scope == 'ready' and not unchecked,
        semantic_correctness='unverified',
        meaning='Public output presence and at least one known assertion; not full semantic-domain coverage.',
    )
