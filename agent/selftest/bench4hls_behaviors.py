"""Closed v4 public-behavior frontend, lowered only through named v3 rules.

This module deliberately separates describing a behavior from deciding whether the
small executable rule library can represent it.  Descriptions outside that
library remain output-total, unresolved declarations.
"""
from __future__ import annotations

import copy

from .bench4hls_architecture import bounds, interface
from .bench4hls_rules import _BooleanParser, compile_rules, rules_status
from .spec import keys, require


TOP_FIELDS = {'schema_version', 'outputs'}
ENTRY_FIELDS = {'behavior', 'evidence', 'uncertainty'}
UNCERTAINTY_FIELDS = {'category', 'detail'}


def _text(value, label):
    require(isinstance(value, str) and 0 < len(value.strip()) <= 2000, label)


def _reason(value, output_name):
    """Bound a generated v3 reason, including obligations' ``name: `` prefix."""
    maximum = 2000 - len(output_name) - 2
    require(maximum > 0, 'Output name leaves no room for an unresolved reason')
    return value[:maximum]


def _uncertainty(value, maximum=2000):
    if value is None:
        return None
    require(isinstance(value, dict), 'Uncertainty must be an object or null')
    keys(value, UNCERTAINTY_FIELDS, 'Uncertainty')
    require(value['category'] in {'missing_information', 'ambiguous'}, 'Invalid uncertainty category')
    _text(value['detail'], 'Uncertainty detail must be a nonempty string of at most 2000 characters')
    # v3 reasons have a 2,000-character transport bound.  Preserve the full
    # v4 uncertainty in ``behavior_spec`` and use only a bounded display reason.
    prefix = value['category'] + ': '
    require(maximum >= len(prefix), 'Output name leaves no room for an uncertainty reason')
    return prefix + value['detail'][:maximum - len(prefix)]


def _public_input(inputs, value, label):
    require(isinstance(value, str), label + ' must be a string')
    require(value in inputs, label + ' references an unknown public input')
    return inputs[value]


def _bit_input(inputs, value, label):
    param = _public_input(inputs, value, label)
    require(not param['signed'] and param['width'] == 1, label + ' must be an unsigned one-bit input')
    return param


def _behavior(behavior, inputs, output):
    """Validate a behavior and return ``(rule, unsupported_reason)``."""
    require(isinstance(behavior, dict), 'Behavior must be an object or null')
    require(isinstance(behavior.get('kind'), str), 'Behavior kind must be a string')
    kind = behavior['kind']
    if kind == 'arithmetic':
        keys(behavior, {'kind', 'operation', 'operands', 'overflow'}, 'Arithmetic behavior')
        require(behavior['operation'] in {'add', 'subtract', 'multiply'}, 'Invalid arithmetic operation')
        require(behavior['overflow'] in {'wrap', 'saturate'}, 'Invalid arithmetic overflow')
        refs = behavior['operands']
        require(isinstance(refs, list) and 2 <= len(refs) <= 8, 'Arithmetic operands require 2..8 inputs')
        params = [_public_input(inputs, value, 'Arithmetic operand') for value in refs]
        if (behavior['operation'] == 'add' and behavior['overflow'] == 'wrap' and
                not output['signed'] and all(not param['signed'] for param in params)):
            return {'kind': 'sum', 'inputs': copy.deepcopy(refs)}, None
        return None, 'unsupported arithmetic behavior'
    if kind == 'logic':
        keys(behavior, {'kind', 'expression', 'domain'}, 'Logic behavior')
        require(behavior['domain'] in {'total', 'partial'}, 'Invalid logic domain')
        ast = _BooleanParser(behavior['expression'], inputs).parse()
        def refs(node):
            if node[0] == 'name': return {node[1]}
            if node[0] == 'const': return set()
            if node[0] == 'not': return refs(node[1])
            return refs(node[1]) | refs(node[2])
        names = refs(ast)
        if behavior['domain'] == 'total' and not output['signed'] and output['width'] == 1 and all(
                not inputs[name]['signed'] and inputs[name]['width'] == 1 for name in names):
            return {'kind': 'boolean', 'expression': behavior['expression']}, None
        return None, 'unsupported logic behavior'
    if kind == 'state':
        fields = {'kind', 'observe', 'initial', 'reset', 'enable', 'update', 'boundary', 'load'}
        keys(behavior, fields, 'State behavior')
        require(behavior['observe'] in {'after', 'before'}, 'Invalid state observation')
        require(behavior['initial'] is None or type(behavior['initial']) is int, 'State initial must be an integer or null')
        reset, enable, update, boundary, load = (behavior[name] for name in ('reset', 'enable', 'update', 'boundary', 'load'))
        if reset is not None:
            require(isinstance(reset, dict), 'Reset must be an object or null'); keys(reset, {'input', 'active', 'value', 'priority'}, 'Reset')
            _bit_input(inputs, reset['input'], 'Reset input')
            require(type(reset['active']) is int and reset['active'] in {0, 1}, 'Invalid reset active')
            require(type(reset['value']) is int and reset['priority'] in {'reset_first', 'enable_first'}, 'Invalid reset')
        if enable is not None:
            require(isinstance(enable, dict), 'Enable must be an object or null'); keys(enable, {'input', 'active'}, 'Enable')
            _bit_input(inputs, enable['input'], 'Enable input')
            require(type(enable['active']) is int and enable['active'] in {0, 1}, 'Invalid enable active')
        require(isinstance(update, dict), 'Update must be an object'); keys(update, {'operation', 'amount'}, 'Update')
        require(update['operation'] in {'add', 'subtract'} and type(update['amount']) is int and update['amount'] > 0, 'Invalid update')
        if boundary is not None:
            require(isinstance(boundary, dict), 'Boundary must be an object or null'); keys(boundary, {'at', 'next'}, 'Boundary')
            require(type(boundary['at']) is int and type(boundary['next']) is int, 'Boundary values must be integers')
        if load is not None:
            require(isinstance(load, dict), 'Load must be an object or null'); keys(load, {'input', 'active', 'value_input', 'priority'}, 'Load')
            _bit_input(inputs, load['input'], 'Load input'); _public_input(inputs, load['value_input'], 'Load value input')
            require(type(load['active']) is int and load['active'] in {0, 1} and load['priority'] in {'load_first', 'reset_first'}, 'Invalid load')
        if boundary is None or boundary['next'] >= boundary['at']:
            return None, 'unsupported state boundary'
        lo, hi = bounds(output)
        domain_ok = lo <= boundary['next'] <= hi and lo <= boundary['at'] <= hi
        reset_ok = reset is None or (lo <= reset['value'] <= hi and boundary['next'] <= reset['value'] <= boundary['at'])
        if (behavior['observe'] == 'after' and behavior['initial'] is None and load is None and
                update == {'operation': 'add', 'amount': 1} and domain_ok and reset_ok):
            return {'kind': 'up_counter', 'lower': boundary['next'], 'upper': boundary['at'],
                    'enable': copy.deepcopy(enable), 'reset': copy.deepcopy(reset)}, None
        return None, 'unsupported state behavior'
    if kind == 'other':
        keys(behavior, {'kind', 'description'}, 'Other behavior')
        _text(behavior['description'], 'Other description must be a nonempty string of at most 2000 characters')
        # The description itself is retained in the v4 source declaration;
        # do not expand it into the bounded v3 reason field.
        return None, 'unsupported other behavior'
    require(False, 'Unsupported behavior kind')


def match_behaviors(raw, problem, top):
    """Validate v4 declarations and deterministically lower supported entries."""
    signature = interface(problem, top)
    outputs = {item['name']: item for item in signature['outputs']}
    inputs = {item['name']: item for item in signature['inputs']}
    keys(raw, TOP_FIELDS, 'Behavior declaration')
    require(type(raw['schema_version']) is int and raw['schema_version'] == 4, 'Expected schema_version 4')
    require(isinstance(raw['outputs'], dict) and set(raw['outputs']) == set(outputs),
            'Output map must contain every exact public output name')
    lowered, decisions = {}, {}
    for name, output in outputs.items():
        entry = raw['outputs'][name]
        require(isinstance(entry, dict), 'Output entry must be an object')
        keys(entry, ENTRY_FIELDS, 'Output ' + name)
        require(isinstance(entry['evidence'], list), 'Evidence must be a list')
        # obligations/rules_status prefix unresolved reasons with ``name: ``.
        uncertain = _uncertainty(entry['uncertainty'], 2000 - len(name) - 2)
        behavior = entry['behavior']
        require(behavior is not None or uncertain is not None, 'Null behavior requires uncertainty')
        rule = unsupported = None
        if behavior is not None:
            require(len(entry['evidence']) > 0, 'Described behavior requires evidence')
            rule, unsupported = _behavior(behavior, inputs, output)
        if uncertain is not None:
            lowered[name] = {'rule': None, 'evidence': copy.deepcopy(entry['evidence']), 'reason': uncertain}
            decisions[name] = {'status': 'uncertain', 'reason': uncertain}
        elif rule is None:
            unsupported = _reason(unsupported, name)
            lowered[name] = {'rule': None, 'evidence': copy.deepcopy(entry['evidence']), 'reason': unsupported}
            decisions[name] = {'status': 'unsupported', 'reason': unsupported}
        else:
            lowered[name] = {'rule': rule, 'evidence': copy.deepcopy(entry['evidence']), 'reason': None}
            decisions[name] = {'status': 'matched', 'reason': None}
    result = {'rules': {'schema_version': 3, 'outputs': lowered}, 'decisions': decisions}
    # Keep matching useful on its own: compile_rules owns source-bound semantic
    # ID validation and remains the sole executable-rule compiler.
    compile_rules(result['rules'], problem, top)
    return result


def compile_behaviors(raw, problem, top):
    matched = match_behaviors(raw, problem, top)
    contract = compile_rules(matched['rules'], problem, top)
    if contract is not None:
        contract.behavior_spec = copy.deepcopy(raw)
        contract.matched_rules_spec = copy.deepcopy(matched['rules'])
        contract.capability_match = copy.deepcopy(matched['decisions'])
    return contract


def behavior_status(raw, problem, top):
    return rules_status(match_behaviors(raw, problem, top)['rules'])
