"""Strict v1 public-input and response contracts; no candidate or dataset access."""
import json
import re
from dataclasses import dataclass
from pathlib import Path

from agent.core.contracts import digest


class SelftestError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise SelftestError(message)


def keys(value, expected, where):
    require(isinstance(value, dict) and set(value) == set(expected),
            f'{where}: expected exactly {sorted(expected)}')


def integer(value, low, high, where):
    require(type(value) is int, f'{where}: expected integer, got {type(value).__name__}')
    require(low <= value <= high, f'{where}: integer outside [{low}, {high}]')
    return value


def text(value, where, limit=4000):
    require(isinstance(value, str) and 0 < len(value.strip()) <= limit, f'{where}: invalid text')
    return value


def parse_json(raw, max_bytes=256_000):
    """Permit one JSON fence, not prose, duplicate keys, NaN, or a second object."""
    require(isinstance(raw, str) and len(raw.encode('utf-8')) <= max_bytes, 'Response too large')
    raw = raw.strip()
    if raw.startswith('```json\n') and raw.endswith('\n```'):
        raw = raw[8:-4]
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    def constant(value):
        raise SelftestError('Non-finite JSON: ' + value)
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError) as error:
        raise SelftestError('Invalid JSON: ' + str(error)) from error


def type_info(ctype):
    require(isinstance(ctype, str), 'C++ type must be text')
    if ctype == 'bool':
        return 0, 1
    match = re.fullmatch(r'(u?)int(8|16|32)_t', ctype)
    if match:
        unsigned, width = bool(match[1]), int(match[2])
    else:
        match = re.fullmatch(r'ap_(u?)int<([0-9]+)>', ctype)
        require(match is not None, 'Unsupported scalar type: ' + ctype)
        unsigned, width = bool(match[1]), int(match[2])
        require(1 <= width <= 32, 'v1 supports ap_int widths 1..32 only')
    return (0, (1 << width) - 1) if unsigned else (-(1 << (width - 1)), (1 << (width - 1)) - 1)


def identifier(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', value)
            and value not in {'true', 'false', 'return', 'if', 'else', 'class', 'main', 'auto', 'bool'},
            'Unsupported identifier')
    return value


def parse_interface(header):
    """Deliberately narrow parser, not an attempt to parse arbitrary C++."""
    clean = re.sub(r'/\*.*?\*/|//[^\n]*', '', header, flags=re.S)
    body = []
    for line in clean.splitlines():
        line = line.strip()
        if line.startswith('#'):
            allowed = (re.fullmatch(r'#\s*include\s*<(?:cstdint|stdint.h|ap_int.h)>', line)
                       or re.fullmatch(r'#\s*pragma\s+once', line)
                       or re.fullmatch(r'#\s*(?:ifndef|define)\s+[A-Za-z_][A-Za-z0-9_]*', line)
                       or re.fullmatch(r'#\s*endif', line))
            require(allowed, 'Unsupported preprocessor directive or dependency')
        else:
            body.append(line)
    clean = re.sub(r'ap_(u?)int\s*<\s*([0-9]+)\s*>', r'ap_\1int<\2>', ' '.join(body)).strip()
    ctype = r'(?:bool|u?int(?:8|16|32)_t|ap_u?int<[0-9]+>)'
    match = re.fullmatch(r'(' + ctype + r')\s+([A-Za-z][A-Za-z0-9_]*)\s*\((.*?)\)\s*;', clean)
    require(match is not None, 'v1 requires one scalar-return function declaration; no arrays, pointers, state or overloads')
    result_type, top, params = match.groups()
    type_info(result_type)
    identifier(top)
    inputs = []
    for param in params.split(','):
        parameter = re.fullmatch(r'(' + ctype + r')\s+([A-Za-z][A-Za-z0-9_]*)', param.strip())
        require(parameter is not None, 'Unsupported function parameter')
        t, name = parameter.groups()
        type_info(t)
        identifier(name)
        inputs.append({'name': name, 'type': t})
    require(1 <= len(inputs) <= 4 and len({p['name'] for p in inputs}) == len(inputs), 'Require 1..4 distinct scalar inputs')
    return {'top_function': top, 'inputs': inputs, 'output_type': result_type}


@dataclass(frozen=True)
class PublicTask:
    problem: str
    interface: str

    @classmethod
    def load(cls, problem, interface):
        # Explicit files only. Never scan siblings or follow a task manifest.
        return cls(Path(problem).read_bytes().decode('utf-8'), Path(interface).read_bytes().decode('utf-8'))

    def validate(self):
        text(self.problem, 'problem', 32000)
        text(self.interface, 'interface', 8000)
        return parse_interface(self.interface)

    def snapshot(self):
        return {k + '_sha256': digest(getattr(self, k).encode('utf-8')) for k in ('problem', 'interface')}


def evidence(items, task):
    require(isinstance(items, list) and 1 <= len(items) <= 8, 'Evidence list required')
    for item in items:
        keys(item, {'source', 'quote'}, 'evidence')
        require(item['source'] in {'problem', 'interface'}, 'Evidence source must be a public input')
        quote = text(item['quote'], 'evidence quote')
        require(quote in getattr(task, item['source']), 'Evidence quote not found in public input')


def validate_contract(contract, task):
    signature = task.validate()
    keys(contract, {'schema_version', 'top_function', 'state', 'inputs', 'output_type', 'rules', 'uncertainties'}, 'contract')
    require(type(contract['schema_version']) is int and contract['schema_version'] == 1, 'Unsupported schema version')
    require(contract['top_function'] == signature['top_function'] and contract['output_type'] == signature['output_type'], 'Contract changed the interface')
    require(contract['state'] == 'stateless', 'Stateful tasks are out of v1 scope')
    require(isinstance(contract['inputs'], list) and len(contract['inputs']) == len(signature['inputs']), 'Input count mismatch')
    for item, parameter in zip(contract['inputs'], signature['inputs']):
        keys(item, {'name', 'type', 'domain', 'evidence'}, 'input')
        require(all(item[k] == parameter[k] for k in ('name', 'type')), 'Input signature mismatch')
        bounds = item['domain']
        require(isinstance(bounds, list) and len(bounds) == 2, 'Domain must be [minimum, maximum]')
        low, high = type_info(item['type'])
        integer(bounds[0], low, high, 'domain minimum')
        integer(bounds[1], bounds[0], high, 'domain maximum')
        evidence(item['evidence'], task)
        if bounds != [low, high]:
            require(any(e['source'] == 'problem' for e in item['evidence']),
                    'A restricted input domain needs problem evidence, not just the interface')
    require(isinstance(contract['rules'], list) and 1 <= len(contract['rules']) <= 32, 'Require 1..32 rules')
    ids = []
    for rule in contract['rules']:
        keys(rule, {'id', 'description', 'evidence'}, 'rule')
        ids.append(identifier(rule['id']))
        text(rule['description'], 'rule description')
        evidence(rule['evidence'], task)
    require(len(set(ids)) == len(ids), 'Duplicate rule IDs')
    require(isinstance(contract['uncertainties'], list) and len(contract['uncertainties']) <= 32, 'Invalid uncertainties')
    for uncertainty in contract['uncertainties']:
        text(uncertainty, 'uncertainty')
    # Quote existence is provenance, NOT proof that a rule follows from the quote.
    return contract
