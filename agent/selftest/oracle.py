"""Bounded integer expression interpreter and reproducible vector expansion. No eval."""
import ast
import itertools
import math
import operator
import random

from .spec import SelftestError, integer, keys, require, text, type_info


BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.BitAnd: operator.and_, ast.BitOr: operator.or_, ast.BitXor: operator.xor,
       ast.LShift: operator.lshift, ast.RShift: operator.rshift}
CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
       ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}
UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Invert: operator.invert, ast.Not: operator.not_}


class Expression:
    def __init__(self, source, names):
        text(source, 'oracle expression', 2000)
        try:
            self.tree = ast.parse(source, mode='eval').body
        except (SyntaxError, RecursionError) as error:
            raise SelftestError('Invalid oracle expression') from error
        nodes = list(ast.walk(self.tree))
        require(len(nodes) <= 128, 'Oracle too complex')
        allowed = (ast.Constant, ast.Name, ast.Load, ast.BinOp, ast.UnaryOp, ast.Compare,
                   ast.IfExp, ast.BoolOp, ast.And, ast.Or, *BIN, *CMP, *UNARY)
        for node in nodes:
            require(isinstance(node, allowed), 'Unsupported oracle syntax: ' + type(node).__name__)
            if isinstance(node, ast.Name):
                require(node.id in names, 'Unknown oracle variable: ' + node.id)
            if isinstance(node, ast.Constant):
                require(type(node.value) in (int, bool) and abs(node.value) < 2**64, 'Invalid oracle constant')

    def __call__(self, values):
        def visit(node):
            if isinstance(node, ast.Constant):
                result = node.value
            elif isinstance(node, ast.Name):
                result = values[node.id]
            elif isinstance(node, ast.BinOp):
                left, right = visit(node.left), visit(node.right)
                if isinstance(node.op, (ast.LShift, ast.RShift)):
                    require(0 <= right <= 63, 'Shift outside 0..63')
                result = BIN[type(node.op)](left, right)
            elif isinstance(node, ast.UnaryOp):
                result = UNARY[type(node.op)](visit(node.operand))
            elif isinstance(node, ast.IfExp):
                result = visit(node.body if visit(node.test) else node.orelse)
            elif isinstance(node, ast.Compare):
                left = visit(node.left)
                result = True
                for op, right_node in zip(node.ops, node.comparators):
                    right = visit(right_node)
                    if not CMP[type(op)](left, right):
                        result = False
                        break
                    left = right
            elif isinstance(node, ast.BoolOp):
                result = all(bool(visit(v)) for v in node.values) if isinstance(node.op, ast.And) else any(bool(visit(v)) for v in node.values)
            else:
                raise SelftestError('Unsupported expression')
            require(type(result) in (int, bool) and abs(result) < 2**64, 'Oracle intermediate exceeds 64-bit magnitude')
            return result
        return int(visit(self.tree))


def expand(plan, contract, max_cases=4096):
    integer(max_cases, 1, 65536, 'max_cases')
    keys(plan, {'schema_version', 'oracle', 'explicit_cases', 'sampling'}, 'test plan')
    require(type(plan['schema_version']) is int and plan['schema_version'] in (1, 2), 'Unsupported plan version')
    keys(plan['oracle'], {'expression', 'rule_ids'}, 'oracle')
    rule_ids = {r['id'] for r in contract['rules']}
    def refs(ids):
        require(isinstance(ids, list) and bool(ids) and all(isinstance(x, str) for x in ids)
                and len(set(ids)) == len(ids) and set(ids) <= rule_ids, 'Unknown, empty or duplicate rule references')
    refs(plan['oracle']['rule_ids'])
    params = contract['inputs']
    names = [p['name'] for p in params]
    expr = Expression(plan['oracle']['expression'], names)
    keys(plan['sampling'], {'mode', 'seed', 'random_cases'}, 'sampling')
    sample = plan['sampling']
    modes = {'exhaustive', 'boundary_random'} if plan['schema_version'] == 1 else {'explicit'}
    require(sample['mode'] in modes, 'Unknown sampling mode')
    integer(sample['seed'], 0, 2**32 - 1, 'seed')
    integer(sample['random_cases'], 0, max_cases, 'random_cases')
    require(sample['mode'] != 'exhaustive' or sample['random_cases'] == 0, 'Exhaustive mode requires random_cases=0')
    require(sample['mode'] != 'explicit' or sample['random_cases'] == 0, 'Explicit mode requires random_cases=0')
    require(isinstance(plan['explicit_cases'], list) and len(plan['explicit_cases']) <= max_cases, 'Invalid explicit_cases')
    vectors = {}
    output_low, output_high = type_info(contract['output_type'])
    def add(values, ids, origin):
        require(isinstance(values, (list, tuple)) and len(values) == len(params), 'Test input arity mismatch')
        for value, param in zip(values, params):
            integer(value, *param['domain'], 'test input')
        key = tuple(values)
        if key in vectors:
            vectors[key]['rule_ids'] = sorted(set(vectors[key]['rule_ids']) | set(ids))
            return
        require(len(vectors) < max_cases, 'Case budget exceeded; no silent sampling/truncation')
        expected = expr(dict(zip(names, values)))
        integer(expected, output_low, output_high, 'oracle output (encode wrap/truncation explicitly)')
        vectors[key] = dict(id=len(vectors), inputs=list(values), expected=expected,
                            rule_ids=list(ids), origin=origin)
    for case in plan['explicit_cases']:
        keys(case, {'inputs', 'rule_ids'}, 'explicit case')
        refs(case['rule_ids'])
        add(case['inputs'], case['rule_ids'], 'explicit')
    domains = [p['domain'] for p in params]
    domain_size = math.prod(high - low + 1 for low, high in domains)
    if sample['mode'] == 'exhaustive':
        require(domain_size <= max_cases, 'Exhaustive domain exceeds case budget')
        for values in itertools.product(*(range(low, high + 1) for low, high in domains)):
            add(values, plan['oracle']['rule_ids'], 'exhaustive')
    elif sample['mode'] == 'boundary_random':
        boundaries = [sorted({v for v in (lo, lo + 1, -1, 0, 1, hi - 1, hi) if lo <= v <= hi}) for lo, hi in domains]
        require(math.prod(map(len, boundaries)) <= max_cases, 'Boundary cross product exceeds budget')
        for values in itertools.product(*boundaries):
            add(values, plan['oracle']['rule_ids'], 'boundary')
        rng = random.Random(sample['seed'])
        for _ in range(sample['random_cases']):
            add([rng.randint(lo, hi) for lo, hi in domains], plan['oracle']['rule_ids'], 'random')
    require(bool(vectors), 'No tests generated')
    asserted = sorted({r for v in vectors.values() for r in v['rule_ids']})
    return list(vectors.values()), dict(case_count=len(vectors), domain_size=domain_size,
        exhaustive_over_declared_domain=len(vectors) == domain_size,
        asserted_rule_ids=asserted, untested_rule_ids=sorted(rule_ids - set(asserted)),
        seed=sample['seed'], semantic_correctness='unverified',
        limitation='Rule IDs and domain coverage describe the proposed oracle, not proof of the specification.')
