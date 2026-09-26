"""Opt-in admission against a separately supplied finite reference.

The reference never enters generation. Agreement certifies only the supplied
finite model, not its interpretation of the public prose or candidate HLS QoR.
All exports remain for manual review; no automatic acceptance/repair is enabled.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import copy
import itertools
import json
from pathlib import Path
import re

from agent.core.contracts import digest, json_digest
from .bench4hls_architecture import bounds, interface, render
from .bench4hls_contract import make_vectors


MAX_FILE_BYTES = 32 * 1024 * 1024
ARTIFACTS = (
    'result.json', 'specification.json', 'matched_rules.json',
    'capability_match.json', 'lowered_obligations.json', 'expanded_graph.json',
    'vectors.json', 'coverage.json', 'output_obligations.json', 'selftest.cpp',
)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _keys(value, fields, label):
    _require(isinstance(value, dict) and set(value) == set(fields), 'Invalid fields: ' + label)


def _text(value, label, limit=2000):
    _require(isinstance(value, str) and 0 < len(value.strip()) <= limit, 'Invalid ' + label)


def _hex(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _bytes(path):
    with Path(path).open('rb') as stream:
        value = stream.read(MAX_FILE_BYTES + 1)
    _require(len(value) <= MAX_FILE_BYTES, 'Artifact exceeds file size limit')
    return value


def _json_bytes(value):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            _require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = item
        return result

    def invalid_constant(value):
        raise ValueError('Non-finite JSON number: ' + value)

    return json.loads(value.decode('utf-8'), object_pairs_hook=unique,
                      parse_constant=invalid_constant)


def _read(path):
    return _json_bytes(_bytes(path))


def _write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _same(left, right):
    # Python equality alone would let True, 1 and 1.0 impersonate one another.
    return json_digest(left) == json_digest(right)


def _ports(signature, kind):
    return [{key: port[key] for key in ('name', 'width', 'signed')} for port in signature[kind]]


@dataclass
class FiniteOracle:
    signature: dict
    states: tuple
    initial_state: str
    input_values: tuple
    table: dict
    provenance: dict

    def step(self, inputs, state):
        outputs, following = self.table[(state, tuple(inputs))]
        return {name: value for name, value in outputs if value is not None}, following


def compile_oracle(raw, problem, top):
    """Validate data only: no expressions, imports, default rows or code execution."""
    _require(isinstance(problem, bytes), 'Public problem must be bytes')
    signature = interface(problem.decode('utf-8'), top)
    _keys(raw, ('schema_version', 'problem_sha256', 'top', 'inputs', 'outputs',
                'states', 'initial_state', 'transitions', 'provenance'), 'finite oracle')
    _require(type(raw['schema_version']) is int and raw['schema_version'] == 1,
             'Unsupported finite oracle schema')
    _require(raw['problem_sha256'] == digest(problem), 'Oracle public problem hash mismatch')
    _require(raw['top'] == top, 'Oracle top mismatch')
    for kind in ('inputs', 'outputs'):
        _require(_same(raw[kind], _ports(signature, kind)), 'Oracle interface mismatch: ' + kind)
    provenance = raw['provenance']
    _keys(provenance, ('origin', 'reference_id', 'source_sha256', 'review_status', 'review_record'),
          'provenance')
    _require(isinstance(provenance['origin'], str) and provenance['origin'] in
             ('external_reference', 'evaluation_fixture'), 'Invalid reference origin')
    _text(provenance['reference_id'], 'reference_id')
    _require(_hex(provenance['source_sha256']), 'Invalid reference source hash')
    _require(isinstance(provenance['review_status'], str) and provenance['review_status'] in
             ('unreviewed', 'fixture_qualified', 'human_reviewed'), 'Invalid review status')
    if provenance['review_record'] is not None:
        _text(provenance['review_record'], 'review_record')
    _require(provenance['review_status'] == 'unreviewed' or provenance['review_record'] is not None,
             'Claimed review requires a record')
    states = raw['states']
    _require(isinstance(states, list) and 1 <= len(states) <= 256, 'Invalid oracle state count')
    for state in states:
        _text(state, 'oracle state name', 128)
    _require(len(set(states)) == len(states), 'Duplicate oracle states')
    _require(isinstance(raw['initial_state'], str) and raw['initial_state'] in states,
             'Invalid initial reference state')
    bits = sum(port['width'] for port in signature['inputs'])
    _require(bits <= 12, 'Finite reference input domain exceeds 4096 tuples')
    input_values = tuple(itertools.product(*[range(bounds(p)[0], bounds(p)[1] + 1)
                                             for p in signature['inputs']]))
    size = len(states) * len(input_values)
    rows = raw['transitions']
    _require(size <= 65536 and isinstance(rows, list) and len(rows) == size,
             'Oracle must cover every state/input pair within the 65536 row limit')
    allowed_inputs = set(input_values)
    output_types = {port['name']: port for port in signature['outputs']}
    table = {}
    for row in rows:
        _keys(row, ('state', 'inputs', 'outputs', 'next_state'), 'transition')
        _require(isinstance(row['state'], str) and row['state'] in states and
                 isinstance(row['next_state'], str) and row['next_state'] in states,
                 'Unknown reference state')
        values = row['inputs']
        _require(isinstance(values, list) and all(type(v) is int for v in values) and
                 tuple(values) in allowed_inputs, 'Invalid transition inputs')
        _keys(row['outputs'], output_types, 'transition outputs')
        for name, value in row['outputs'].items():
            lo, hi = bounds(output_types[name])
            _require(value is None or type(value) is int and lo <= value <= hi,
                     'Invalid reference output: ' + name)
        key = (row['state'], tuple(values))
        _require(key not in table, 'Duplicate state/input transition')
        table[key] = (tuple(row['outputs'].items()), row['next_state'])
    return FiniteOracle(copy.deepcopy(signature), tuple(states), raw['initial_state'],
                        input_values, table, copy.deepcopy(provenance))


def _budgets(max_product_states, max_transitions):
    _require(type(max_product_states) is int and 1 <= max_product_states <= 65536,
             'Invalid product state budget')
    _require(type(max_transitions) is int and 1 <= max_transitions <= 1_000_000,
             'Invalid transition budget')


def _difference(wanted, got):
    for name in sorted(set(wanted) | set(got)):
        if name not in wanted:
            return dict(kind='unsafe_assertion', output=name, actual=got[name], expected=None)
        if name not in got:
            return dict(kind='missing_assertion', output=name, actual=None, expected=wanted[name])
        if type(got[name]) is not int or got[name] != wanted[name]:
            return dict(kind='value_mismatch', output=name, actual=got[name], expected=wanted[name])
    return None


def _prefix(parents, key, inputs):
    prefix = [list(inputs)]
    while parents[key] is not None:
        key, preceding = parents[key]
        prefix.append(list(preceding))
    prefix.reverse()
    return prefix


def compare_finite(contract, oracle, *, max_product_states=4096, max_transitions=65536):
    """Exhaust reachable product states, or return a witness/budget limitation."""
    _budgets(max_product_states, max_transitions)
    _require(_same(contract.signature, oracle.signature), 'Comparison interface mismatch')
    names = tuple(contract.initial_state())
    initial = (oracle.initial_state, tuple(contract.initial_state()[n] for n in names))
    parents, queue = {initial: None}, deque([initial])
    result = dict(status='inconclusive', complete=False, checked_transitions=0,
                  reachable_product_states=1, input_domain_size=len(oracle.input_values),
                  max_product_states=max_product_states, max_transitions=max_transitions)
    while queue:
        key = queue.popleft()
        ref_state, compiled_state = key
        for inputs in oracle.input_values:
            if result['checked_transitions'] >= max_transitions:
                return dict(result, reason='transition_budget_exhausted')
            wanted, ref_next = oracle.step(inputs, ref_state)
            got, next_state, _ = contract.step(list(inputs), dict(zip(names, compiled_state)))
            result['checked_transitions'] += 1
            difference = _difference(wanted, got)
            if difference:
                return dict(result, status='counterexample', counterexample=dict(
                    difference, inputs=list(inputs), prefix=_prefix(parents, key, inputs),
                    reference_state=ref_state, generated_state=dict(zip(names, compiled_state))))
            following = (ref_next, tuple(next_state[n] for n in names))
            if following not in parents:
                if len(parents) >= max_product_states:
                    return dict(result, reason='product_state_budget_exhausted')
                parents[following] = (key, inputs)
                queue.append(following)
                result['reachable_product_states'] = len(parents)
    return dict(result, status='equivalent', complete=True,
                meaning='Agreement with supplied finite reference and graph executor only')


def _check_vectors(vectors, oracle):
    state, assertions = oracle.initial_state, 0
    for index, row in enumerate(vectors):
        wanted, state = oracle.step(row['inputs'], state)
        difference = _difference(wanted, row['expected'])
        if difference:
            return dict(status='counterexample', calls=index + 1, assertions=assertions,
                        counterexample=dict(difference, call=index, inputs=row['inputs'],
                                            prefix=[v['inputs'] for v in vectors[:index + 1]]))
        assertions += len(row['expected'])
    return dict(status='consistent', calls=len(vectors), assertions=assertions)


def _stored(directory, name, expected):
    _require(_same(_read(directory / name), expected), 'Replayed artifact mismatch: ' + name)


def _replay(problem, top, directory):
    from .bench4hls_behaviors import compile_behaviors, match_behaviors
    from .bench4hls_rules import compile_rules
    from .bench4hls_obligations import public_output_coverage

    receipt = _read(directory / 'result.json')
    _require(isinstance(receipt, dict), 'Invalid generation receipt')
    signed = {k: v for k, v in receipt.items() if k != 'receipt_id'}
    _require(receipt.get('receipt_id') == json_digest(signed), 'Generation receipt hash mismatch')
    _require(receipt.get('candidate_visible') is False and
             receipt.get('official_testbench_visible') is False, 'Generation provenance not public-only')
    status = receipt.get('status')
    if status == 'failed':
        _require(not (directory / 'selftest.cpp').exists(), 'Failed generation retains an executable artifact')
        return receipt, None, None
    _require(status in ('abstained', 'generated_unreviewed'), 'Unsupported generation status')
    _require(receipt.get('problem_sha256') == digest(problem), 'Generation public problem hash mismatch')
    representation = receipt.get('representation')
    _require(representation in ('rules', 'behaviors'), 'Gate supports rules and behaviors declarations')
    raw = _read(directory / 'specification.json')
    compiler = compile_behaviors if representation == 'behaviors' else compile_rules
    contract = compiler(raw, problem.decode('utf-8'), top)
    if status != 'abstained' or 'specification_sha256' in receipt:
        _require(receipt.get('specification_sha256') == json_digest(raw), 'Specification hash mismatch')
    if representation == 'behaviors':
        matched = match_behaviors(raw, problem.decode('utf-8'), top)
        for name, value, field in (
            ('matched_rules.json', matched['rules'], 'matched_rules_sha256'),
            ('capability_match.json', matched['decisions'], 'capability_match_sha256'),
        ):
            _stored(directory, name, value)
            _require(receipt.get(field) == json_digest(value), 'Matching receipt hash mismatch: ' + name)
    if status == 'abstained':
        _require(contract is None and not (directory / 'selftest.cpp').exists(), 'Invalid abstention artifacts')
        return receipt, None, None
    _require(contract is not None, 'Generated receipt has no executable contract')
    _stored(directory, 'expanded_graph.json', contract.raw)
    _require(receipt.get('expanded_graph_sha256') == json_digest(contract.raw), 'Graph receipt hash mismatch')
    _stored(directory, 'lowered_obligations.json', contract.lowered_obligations_spec)
    max_calls, seed = receipt.get('max_calls'), receipt.get('sampling_seed')
    _require(type(max_calls) is int and 1 <= max_calls <= 1024 and type(seed) is int,
             'Invalid recorded generation budget')
    vectors, coverage = make_vectors(contract, max_calls=max_calls, seed=seed, cover_counter_wrap=True)
    _stored(directory, 'vectors.json', vectors)
    _stored(directory, 'coverage.json', coverage)
    _require(_same(receipt.get('coverage'), coverage), 'Coverage receipt mismatch')
    scope = contract.raw['decision']
    public_coverage = public_output_coverage(contract.signature, contract, coverage, scope)
    _stored(directory, 'output_obligations.json', public_coverage)
    _require(_same(receipt.get('public_output_coverage'), public_coverage), 'Public coverage receipt mismatch')
    expected_source = render(contract.signature, vectors).encode('utf-8')
    source = _bytes(directory / 'selftest.cpp')
    _require(source == expected_source and receipt.get('source_sha256') == digest(source),
             'Rendered testbench source mismatch')
    return receipt, contract, vectors


def _base(problem, top):
    return dict(version=1, status='inconclusive', problem_sha256=digest(problem), top=top,
                automatic_acceptance_allowed=False, repair_feedback_allowed=False,
                export_for_review_allowed=False, reference_review_required=True,
                reference_authority='caller_supplied_not_authenticated',
                gate_source_sha256=digest(Path(__file__).read_bytes()))


def assess_generation(problem, top, generation, *, oracle_path=None,
                      expected_oracle_sha256=None, max_product_states=4096,
                      max_transitions=65536):
    """Read-only admission; even full agreement remains subject to reference review."""
    _require(isinstance(problem, bytes), 'Public problem must be bytes')
    _budgets(max_product_states, max_transitions)
    result = _base(problem, top)
    directory = Path(generation)
    try:
        receipt, contract, vectors = _replay(problem, top, directory)
        result['generation_receipt_id'] = receipt['receipt_id']
        result['generation_status'] = receipt['status']
        result['artifact_sha256'] = {name: digest(_bytes(directory / name)) for name in ARTIFACTS
                                     if (directory / name).is_file()}
        if receipt['status'] == 'failed':
            return dict(result, status='generation_failed')
        if receipt['status'] == 'abstained':
            return dict(result, status='abstained')
    except (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError) as error:
        return dict(result, status='invalid_artifact', message=str(error))
    if oracle_path is None or expected_oracle_sha256 is None:
        return dict(result, status='needs_reference', reason='A separately pinned finite reference is required')
    try:
        _require(_hex(expected_oracle_sha256), 'Invalid expected reference hash')
        reference_bytes = _bytes(oracle_path)
        _require(digest(reference_bytes) == expected_oracle_sha256, 'Reference file hash mismatch')
        oracle = compile_oracle(_json_bytes(reference_bytes), problem, top)
        result.update(oracle_sha256=expected_oracle_sha256,
                      oracle_provenance_claim=copy.deepcopy(oracle.provenance))
    except (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError) as error:
        return dict(result, status='invalid_reference', message=str(error))
    try:
        comparison = compare_finite(contract, oracle, max_product_states=max_product_states,
                                    max_transitions=max_transitions)
        trace = _check_vectors(vectors, oracle)
        result.update(comparison=comparison, emitted_vectors=trace)
        if comparison['status'] == 'counterexample' or trace['status'] == 'counterexample':
            return dict(result, status='counterexample')
        if comparison['status'] != 'equivalent':
            return dict(result, status='inconclusive', reason=comparison.get('reason'))
        if (receipt.get('coverage_scope') != 'ready' or
                receipt['public_output_coverage'].get('complete') is not True or
                receipt['coverage'].get('missing') or
                not all(receipt['coverage']['per_output'].values())):
            return dict(result, status='inconclusive', reason='Incomplete declared or emitted test coverage')
        return dict(result, status='reference_checked', export_for_review_allowed=True,
                    qualification_meaning='Finite-reference agreement; emitted tests remain finite and require review')
    except (ValueError, TypeError, KeyError, IndexError, RecursionError) as error:
        return dict(result, status='invalid_artifact', message=str(error))


def review_testbench(problem, top, generation, output, **gate_options):
    """Assess snapshots, then export precisely the checked bytes into a new directory."""
    output, generation = Path(output), Path(generation)
    output.mkdir(parents=True, exist_ok=False)
    saved = output / 'inputs'
    saved.mkdir()
    (saved / 'problem.txt').write_bytes(problem)
    snapshot = saved / 'generation'
    snapshot.mkdir()
    options = dict(gate_options)
    try:
        for name in ARTIFACTS:
            if (generation / name).exists():
                (snapshot / name).write_bytes(_bytes(generation / name))
    except (OSError, ValueError) as error:
        report = dict(_base(problem, top), status='invalid_artifact', message=str(error))
    else:
        oracle_path = options.get('oracle_path')
        if oracle_path is not None:
            try:
                (saved / 'oracle.json').write_bytes(_bytes(oracle_path))
            except (OSError, ValueError) as error:
                report = dict(_base(problem, top), status='invalid_reference', message=str(error))
            else:
                options['oracle_path'] = saved / 'oracle.json'
                report = assess_generation(problem, top, snapshot, **options)
        else:
            report = assess_generation(problem, top, snapshot, **options)
    if report['export_for_review_allowed']:
        checked = output / 'checked'
        checked.mkdir()
        (checked / 'selftest.cpp').write_bytes(_bytes(snapshot / 'selftest.cpp'))
        report['export_path'] = 'checked/selftest.cpp'
        report['export_sha256'] = digest(_bytes(checked / 'selftest.cpp'))
    report['receipt_id'] = json_digest(report)
    _write(output / 'report.json', report)
    return report


def generate_guarded_behaviors(problem, top, output, *, model, runtime, deadline,
                               max_calls=1024, seed=20260930, **gate_options):
    """Public-only generation followed by a separate, non-feedback reference gate."""
    from .bench4hls_contract_generation import generate_behaviors

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    generation = generate_behaviors(problem, top, output / 'draft', model=model,
                                    runtime=runtime, deadline=deadline,
                                    max_calls=max_calls, seed=seed)
    review = review_testbench(problem, top, output / 'draft', output / 'review', **gate_options)
    result = dict(version=1, status=review['status'], generation=generation, review=review,
                  automatic_acceptance_allowed=False, repair_feedback_allowed=False,
                  export_for_review_allowed=review['export_for_review_allowed'])
    result['receipt_id'] = json_digest(result)
    _write(output / 'result.json', result)
    return result
