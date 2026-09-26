"""Closed v3 named-rule frontend, lowered through the v2 obligations compiler."""
from __future__ import annotations

import copy
import re

from .bench4hls_architecture import interface
from .bench4hls_obligations import compile_obligations, obligation_status
from .spec import keys, require


ENTRY_FIELDS = {'rule', 'evidence', 'reason'}


def rules_status(raw):
    """Return the ordinary obligation scope, based solely on null rules."""
    adapted = {'outputs': {
        name: {'value': None if entry['rule'] is None else 'rule',
               'reason': entry['reason']}
        for name, entry in raw['outputs'].items()}}
    return obligation_status(adapted)


class _BooleanParser:
    _ident = re.compile(r'[A-Za-z_]\w*')

    def __init__(self, expression, inputs):
        require(isinstance(expression, str) and len(expression) <= 2048,
                'Boolean expression must be a string of at most 2048 characters')
        self.inputs = inputs
        self.tokens = self._lex(expression)
        self.at = 0
        self.depth = 0

    def _lex(self, text):
        result = []
        index = 0
        while index < len(text):
            if text[index].isspace():
                index += 1; continue
            match = self._ident.match(text, index)
            if match:
                name = match.group()
                require(name in self.inputs, 'Boolean expression references an unknown public input')
                result.append(('name', name)); index = match.end(); continue
            char = text[index]
            if char in '01':
                # Digits are closed tokens: 10 and 2 are never silently split.
                require(index + 1 == len(text) or not text[index + 1].isalnum() and text[index + 1] != '_',
                        'Invalid Boolean constant')
                result.append(('const', int(char))); index += 1; continue
            if char in '!^()': result.append((char, char)); index += 1; continue
            if char in '&|':
                if index + 1 < len(text) and text[index + 1] == char: index += 1
                result.append((char, char)); index += 1; continue
            require(False, 'Invalid Boolean token')
        require(result and len(result) <= 256, 'Boolean expression requires 1..256 tokens')
        return result

    def _take(self, kind):
        if self.at < len(self.tokens) and self.tokens[self.at][0] == kind:
            value = self.tokens[self.at][1]; self.at += 1; return value
        return None

    def parse(self):
        value = self._or()
        require(self.at == len(self.tokens), 'Boolean expression was not fully consumed')
        return value

    def _or(self):
        value = self._xor()
        while self._take('|') is not None: value = ('or', value, self._xor())
        return value

    def _xor(self):
        value = self._and()
        while self._take('^') is not None: value = ('xor', value, self._and())
        return value

    def _and(self):
        value = self._unary()
        while self._take('&') is not None: value = ('and', value, self._unary())
        return value

    def _unary(self):
        if self._take('!') is not None:
            self.depth += 1; require(self.depth <= 32, 'Boolean expression nesting exceeds 32')
            value = ('not', self._unary())
            self.depth -= 1
            return value
        if self._take('(') is not None:
            self.depth += 1; require(self.depth <= 32, 'Boolean expression nesting exceeds 32')
            value = self._or()
            require(self._take(')') is not None, 'Unclosed Boolean parenthesis')
            self.depth -= 1
            return value
        if self.at < len(self.tokens) and self.tokens[self.at][0] in {'name', 'const'}:
            value = self.tokens[self.at]; self.at += 1; return value
        require(False, 'Boolean operand required')


def compile_rules(raw, problem, top):
    signature = interface(problem, top)
    keys(raw, {'schema_version', 'outputs'}, 'Named rule declaration')
    require(type(raw['schema_version']) is int and raw['schema_version'] == 3, 'Expected schema_version 3')
    outputs = {item['name']: item for item in signature['outputs']}
    inputs = {item['name']: item for item in signature['inputs']}
    require(isinstance(raw['outputs'], dict) and set(raw['outputs']) == set(outputs),
            'Output map must contain every exact public output name')

    components = []
    def add(item):
        require(len(components) < 32, 'Lowered rules exceed 32 components')
        components.append(item); return 'c' + str(len(components) - 1)
    def component(kind, evidence, **fields):
        return add(dict(kind=kind, evidence=list(evidence), **fields))
    def public(name):
        require(name in inputs, 'Rule references an unknown public input')
        return 'in:' + name
    def unsigned(param, label):
        require(not param['signed'], label + ' must be unsigned')
    def predicate(name, label):
        require(name in inputs, label + ' references an unknown public input')
        p = inputs[name]; require(not p['signed'] and p['width'] == 1, label + ' must be an unsigned one-bit input')

    lowered_outputs = {}
    for name, output in outputs.items():
        entry = raw['outputs'][name]
        keys(entry, ENTRY_FIELDS, 'Output ' + name)
        require(isinstance(entry['evidence'], list), 'Evidence must be a list')
        rule = entry['rule']
        if rule is None:
            require(isinstance(entry['reason'], str) and 0 < len(entry['reason'].strip()) <= 2000,
                    'Unresolved output requires a nonempty reason')
            lowered_outputs[name] = dict(value=None, observe=None, evidence=copy.deepcopy(entry['evidence']), reason=entry['reason'])
            continue
        require(entry['reason'] is None and isinstance(rule, dict), 'Checked output requires rule and null reason')
        keys(rule, {'kind'} | ({'inputs'} if rule.get('kind') == 'sum' else
                              {'expression'} if rule.get('kind') == 'boolean' else
                              {'lower', 'upper', 'enable', 'reset'} if rule.get('kind') == 'up_counter' else set()),
             'Rule')
        evidence = entry['evidence']
        if rule['kind'] == 'sum':
            unsigned(output, 'Sum output')
            refs = rule['inputs']; require(isinstance(refs, list) and 2 <= len(refs) <= 8, 'sum requires 2..8 inputs')
            work = output['width']; converted = []
            for input_name in refs:
                require(isinstance(input_name, str), 'sum input names must be strings'); p = inputs.get(input_name)
                require(p is not None, 'sum references an unknown public input'); unsigned(p, 'Sum input')
                work = max(work, p['width'])
            for input_name in refs:
                ref = public(input_name)
                if inputs[input_name]['width'] != work:
                    ref = component('cast', evidence, input=ref, width=work, signed=False)
                converted.append(ref)
            value = converted[0]
            for ref in converted[1:]: value = component('binary', evidence, operation='add', a=value, b=ref)
            if output['width'] != work: value = component('cast', evidence, input=value, width=output['width'], signed=False)
        elif rule['kind'] == 'boolean':
            require(not output['signed'] and output['width'] == 1, 'Boolean output must be unsigned one-bit')
            ast = _BooleanParser(rule['expression'], inputs).parse()
            one = [None]
            def emit(node):
                kind = node[0]
                if kind == 'name': predicate(node[1], 'Boolean input'); return public(node[1])
                if kind == 'const':
                    if one[0] is None or node[1] == 0:
                        return component('const', evidence, width=1, signed=False, value=node[1])
                    return one[0]
                if kind == 'not':
                    if one[0] is None: one[0] = component('const', evidence, width=1, signed=False, value=1)
                    return component('binary', evidence, operation='xor', a=emit(node[1]), b=one[0])
                return component('binary', evidence, operation=kind, a=emit(node[1]), b=emit(node[2]))
            value = emit(ast)
        else:
            require(rule['kind'] == 'up_counter', 'Unsupported rule kind')
            require(type(rule['lower']) is int and type(rule['upper']) is int, 'Counter bounds must be integers')
            enable, reset = rule['enable'], rule['reset']
            if enable is not None:
                keys(enable, {'input', 'active'}, 'Enable'); require(isinstance(enable['input'], str), 'Enable input must be a string'); predicate(enable['input'], 'Enable')
                require(type(enable['active']) is int and enable['active'] in {0, 1}, 'Invalid enable active')
            if reset is not None:
                keys(reset, {'input', 'active', 'value', 'priority'}, 'Reset'); require(isinstance(reset['input'], str), 'Reset input must be a string'); predicate(reset['input'], 'Reset')
                require(type(reset['active']) is int and reset['active'] in {0, 1} and type(reset['value']) is int and reset['priority'] in {'reset_first', 'enable_first'}, 'Invalid reset')
            lowered_enable = None if enable is None else dict(input=public(enable['input']), active=enable['active'])
            lowered_reset = (None if reset is None else dict(input=public(reset['input']), active=reset['active'],
                                                              value=reset['value'], priority=reset['priority']))
            value = component('counter', evidence, width=output['width'], signed=output['signed'], lower=rule['lower'], upper=rule['upper'],
                              enable=lowered_enable, reset=lowered_reset)
        lowered_outputs[name] = dict(value=value, observe='after', evidence=copy.deepcopy(evidence), reason=None)
    lowered = dict(schema_version=2, components=components, outputs=lowered_outputs)
    contract = compile_obligations(lowered, problem, top)
    if contract is not None:
        contract.rule_spec = copy.deepcopy(raw)
        contract.lowered_obligations_spec = copy.deepcopy(lowered)
    return contract
