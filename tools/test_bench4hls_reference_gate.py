"""Finite external-reference admission gate regression tests.

These use the ordinary public behavior generator only to make a realistic draft;
the oracle itself is deliberately a small data-only transition table.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest

from agent.selftest.bench4hls_behaviors import compile_behaviors
from agent.selftest.bench4hls_contract import compile_contract
from agent.selftest.bench4hls_contract_generation import generate_behaviors
from agent.core.contracts import json_digest
from agent.selftest.bench4hls_reference_gate import (
    assess_generation, compare_finite, compile_oracle, generate_guarded_behaviors,
    review_testbench,
)
from tools.test_bench4hls_contract_generation import Responses


PARITY_PROBLEM = ('Three one-bit inputs have odd parity.\n'
                  'void TopModule(bool a, bool b, bool c, bool &q)')
COUNTER_PROBLEM = ('A two-bit counter starts unknown; active-low reset loads zero. '
                   'When enabled it increments and q observes the updated count.\n'
                   'void Counter(bool reset_n, bool enable, ap_uint<2> &q)')
SIGNED_PROBLEM = 'Signed identity.\nvoid Signed(ap_int<2> x, ap_int<2> &q)'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def behavior(expression):
    return {'schema_version': 4, 'outputs': {'q': {
        'behavior': {'kind': 'logic', 'expression': expression, 'domain': 'total'},
        'evidence': ['s0001'], 'uncertainty': None}}}


def parity_table(problem=PARITY_PROBLEM, *, provenance=None):
    raw = problem.encode('utf-8')
    rows = []
    for a in range(2):
        for b in range(2):
            for c in range(2):
                rows.append({'state': 'S', 'inputs': [a, b, c],
                             'outputs': {'q': a ^ b ^ c}, 'next_state': 'S'})
    return {'schema_version': 1, 'problem_sha256': sha(raw), 'top': 'TopModule',
            'inputs': [dict(name=x, width=1, signed=False) for x in ('a', 'b', 'c')],
            'outputs': [dict(name='q', width=1, signed=False)], 'states': ['S'],
            'initial_state': 'S', 'transitions': rows,
            'provenance': provenance or {'origin': 'external_reference', 'reference_id': 'parity-v1',
                'source_sha256': '0' * 64, 'review_status': 'unreviewed', 'review_record': None}}


def counter_table():
    rows = []
    for state in ('U', '0', '1', '2', '3'):
        for reset_n in range(2):
            for enable in range(2):
                if not reset_n:
                    next_state = '0'
                elif state == 'U' or not enable:
                    next_state = state
                else:
                    next_state = str((int(state) + 1) % 4)
                rows.append({'state': state, 'inputs': [reset_n, enable],
                             'outputs': {'q': None if next_state == 'U' else int(next_state)},
                             'next_state': next_state})
    raw = COUNTER_PROBLEM.encode()
    return {'schema_version': 1, 'problem_sha256': sha(raw), 'top': 'Counter',
            'inputs': [dict(name='reset_n', width=1, signed=False), dict(name='enable', width=1, signed=False)],
            'outputs': [dict(name='q', width=2, signed=False)], 'states': ['U', '0', '1', '2', '3'],
            'initial_state': 'U', 'transitions': rows,
            'provenance': {'origin': 'external_reference', 'reference_id': 'counter-v1',
                'source_sha256': '1' * 64, 'review_status': 'unreviewed', 'review_record': None}}


def counter_behavior(*, upper=3):
    return {'schema_version': 4, 'outputs': {'q': {'behavior': {
        'kind': 'state', 'observe': 'after', 'initial': None,
        'reset': {'input': 'reset_n', 'active': 0, 'value': 0, 'priority': 'reset_first'},
        'enable': {'input': 'enable', 'active': 1}, 'update': {'operation': 'add', 'amount': 1},
        'boundary': {'at': upper, 'next': 0}, 'load': None},
        'evidence': ['s0001'], 'uncertainty': None}}}


def signed_table():
    raw = SIGNED_PROBLEM.encode()
    return {'schema_version': 1, 'problem_sha256': sha(raw), 'top': 'Signed',
            'inputs': [dict(name='x', width=2, signed=True)],
            'outputs': [dict(name='q', width=2, signed=True)], 'states': ['S'], 'initial_state': 'S',
            'transitions': [{'state': 'S', 'inputs': [x], 'outputs': {'q': x}, 'next_state': 'S'}
                            for x in (-2, -1, 0, 1)],
            'provenance': {'origin': 'external_reference', 'reference_id': 'signed-v1',
                'source_sha256': '2' * 64, 'review_status': 'unreviewed', 'review_record': None}}


class ReferenceGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def oracle(self, raw=None, problem=PARITY_PROBLEM, top='TopModule'):
        return compile_oracle(raw or parity_table(problem), problem.encode(), top)

    def contract(self, expression):
        return compile_behaviors(behavior(expression), PARITY_PROBLEM, 'TopModule')

    def assert_withheld(self, report):
        self.assertFalse(report['automatic_acceptance_allowed'])
        self.assertFalse(report['repair_feedback_allowed'])

    def test_odd_parity_agrees_exhaustively(self):
        report = compare_finite(self.contract('a ^ b ^ c'), self.oracle())
        self.assertEqual(report['status'], 'equivalent')
        self.assertTrue(report['complete'])

    def test_exactly_two_shared_mistake_has_counterexample(self):
        # The erroneous primary and blind response agrees with itself, but not the table.
        exactly_two = '(a & b & !c) | (a & !b & c) | (!a & b & c)'
        report = compare_finite(self.contract(exactly_two), self.oracle())
        self.assertEqual(report['status'], 'counterexample')
        self.assertEqual(report['counterexample']['inputs'], [0, 0, 1])

    def test_unknown_counter_has_no_assertion_before_reset(self):
        oracle = self.oracle(counter_table(), COUNTER_PROBLEM, 'Counter')
        first, state = oracle.step((1, 0), 'U')
        self.assertNotIn('q', first)
        second, state = oracle.step((0, 0), state)
        self.assertEqual(second['q'], 0)

    def test_oracle_requires_complete_unique_typed_rows(self):
        cases = []
        duplicate = parity_table(); duplicate['transitions'].append(copy.deepcopy(duplicate['transitions'][0])); cases.append(duplicate)
        missing = parity_table(); missing['transitions'].pop(); cases.append(missing)
        field = parity_table(); field['transitions'][0]['outputs'] = {}; cases.append(field)
        boolean = parity_table(); boolean['transitions'][0]['inputs'][0] = True; cases.append(boolean)
        unsigned = parity_table(); unsigned['transitions'][0]['inputs'][0] = -1; cases.append(unsigned)
        for raw in cases:
            with self.subTest(raw=raw['transitions'][0]):
                with self.assertRaises(ValueError): self.oracle(raw)

    def test_oracle_rejects_wrong_public_binding_and_provenance(self):
        cases = []
        wrong_bytes = parity_table(); wrong_bytes['problem_sha256'] = 'f' * 64; cases.append(wrong_bytes)
        wrong_top = parity_table(); wrong_top['top'] = 'Other'; cases.append(wrong_top)
        wrong_interface = parity_table(); wrong_interface['inputs'][0]['width'] = 2; cases.append(wrong_interface)
        wrong_hash = parity_table(); wrong_hash['provenance']['source_sha256'] = 'x'; cases.append(wrong_hash)
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError): self.oracle(raw)

    def test_signed_table_rejects_wrong_width_noninteger_and_duplicate_replacement(self):
        self.assertEqual(self.oracle(signed_table(), SIGNED_PROBLEM, 'Signed').input_values,
                         ((-2,), (-1,), (0,), (1,)))
        cases = []
        wrong_width = signed_table(); wrong_width['outputs'][0]['width'] = 3; cases.append(wrong_width)
        boolean = signed_table(); boolean['transitions'][0]['outputs']['q'] = True; cases.append(boolean)
        floating = signed_table(); floating['transitions'][0]['outputs']['q'] = 1.0; cases.append(floating)
        duplicate = signed_table(); duplicate['transitions'][-1] = copy.deepcopy(duplicate['transitions'][0]); cases.append(duplicate)
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError): self.oracle(raw, SIGNED_PROBLEM, 'Signed')

    def test_stateful_reset_polarity_enable_priority_and_observation_are_detected(self):
        oracle = self.oracle(counter_table(), COUNTER_PROBLEM, 'Counter')
        base = {'kind': 'state', 'observe': 'after', 'initial': None,
                'reset': {'input': 'reset_n', 'active': 0, 'value': 0, 'priority': 'reset_first'},
                'enable': {'input': 'enable', 'active': 1},
                'update': {'operation': 'add', 'amount': 1}, 'boundary': {'at': 3, 'next': 0}, 'load': None}
        for change in (
            {'reset': {'input': 'reset_n', 'active': 1, 'value': 0, 'priority': 'reset_first'}},
            {'reset': {'input': 'reset_n', 'active': 0, 'value': 0, 'priority': 'enable_first'}},
            {'observe': 'before'},
        ):
            state = copy.deepcopy(base); state.update(change)
            if change == {'observe': 'before'}:
                # The behavior frontend intentionally refuses old-state rules;
                # lower a valid graph and then exercise comparison's phase check.
                state['observe'] = 'after'
            raw = {'schema_version': 4, 'outputs': {'q': {'behavior': state,
                'evidence': ['s0001'], 'uncertainty': None}}}
            contract = compile_behaviors(raw, COUNTER_PROBLEM, 'Counter')
            if change == {'observe': 'before'}:
                graph = copy.deepcopy(contract.raw)
                graph['outputs'][0]['observe'] = 'before'
                contract = compile_contract(graph, COUNTER_PROBLEM, 'Counter')
            with self.subTest(change=change):
                self.assertEqual(compare_finite(contract, oracle)['status'], 'counterexample')

    def test_comparison_budget_exhaustion_is_inconclusive(self):
        report = compare_finite(self.contract('a ^ b ^ c'), self.oracle(), max_product_states=1, max_transitions=1)
        self.assertEqual(report['status'], 'inconclusive')
        self.assertFalse(report['complete'])

    def test_counter_product_budget_is_inconclusive_separately_from_transition_budget(self):
        raw = counter_behavior()
        contract = compile_behaviors(raw, COUNTER_PROBLEM, 'Counter')
        report = compare_finite(contract, self.oracle(counter_table(), COUNTER_PROBLEM, 'Counter'),
                                max_product_states=1, max_transitions=256)
        self.assertEqual(report['status'], 'inconclusive')
        self.assertEqual(report['reason'], 'product_state_budget_exhausted')

    def generated(self, responses):
        output = self.root / ('draft-' + str(len(list(self.root.glob('draft-*')))))
        result = generate_behaviors(PARITY_PROBLEM.encode(), 'TopModule', output, model=Responses(responses),
                                    runtime={}, deadline=time.monotonic() + 30, max_calls=32)
        return result, output

    def write_oracle(self, raw=None):
        path = self.root / 'oracle.json'; data = json.dumps(raw or parity_table(), sort_keys=True).encode()
        path.write_bytes(data)
        return path, sha(data)

    def test_missing_reference_needs_reference(self):
        generation, output = self.generated([behavior('a ^ b ^ c'), behavior('a ^ b ^ c')])
        report = assess_generation(PARITY_PROBLEM.encode(), 'TopModule', output)
        self.assertEqual(report['status'], 'needs_reference')
        self.assert_withheld(report)

    def test_oracle_without_matching_pin_is_needs_reference(self):
        _, output = self.generated([behavior('a ^ b ^ c'), behavior('a ^ b ^ c')])
        oracle_path, _ = self.write_oracle()
        report = assess_generation(PARITY_PROBLEM.encode(), 'TopModule', output, oracle_path=oracle_path)
        self.assertEqual(report['status'], 'needs_reference')
        self.assert_withheld(report)

    def test_shared_bad_draft_cannot_export_checked_cpp(self):
        bad = behavior('(a & b & !c) | (a & !b & c) | (!a & b & c)')
        generation, output = self.generated([bad, bad])
        self.assertEqual(generation['review']['status'], 'consistent_on_declared_domain')
        oracle_path, expected = self.write_oracle()
        review = self.root / 'review-bad'
        report = review_testbench(PARITY_PROBLEM.encode(), 'TopModule', output, review,
                                  oracle_path=oracle_path, expected_oracle_sha256=expected)
        self.assertEqual(report['status'], 'counterexample')
        self.assertFalse((review / 'checked' / 'selftest.cpp').exists())
        self.assert_withheld(report)

    def test_late_counter_wrap_is_found_beyond_two_saved_stimuli(self):
        output = self.root / 'late-wrap'
        wrong = counter_behavior(upper=2)
        generation = generate_behaviors(COUNTER_PROBLEM.encode(), 'Counter', output,
                                        model=Responses([wrong, wrong]), runtime={},
                                        deadline=time.monotonic()+30, max_calls=2)
        saved = json.loads((output / 'vectors.json').read_text())
        self.assertEqual(len(saved), 2)
        oracle_path, expected = self.write_oracle(counter_table())
        report = assess_generation(COUNTER_PROBLEM.encode(), 'Counter', output,
                                   oracle_path=oracle_path, expected_oracle_sha256=expected)
        self.assertEqual(report['status'], 'counterexample')
        self.assertGreaterEqual(len(report['comparison']['counterexample']['prefix']), 4)
        self.assertTrue(report['emitted_vectors']['status'] == 'consistent')
        self.assert_withheld(report)

    def test_wrong_oracle_pin_is_invalid_reference_and_exports_nothing(self):
        _, output = self.generated([behavior('a ^ b ^ c'), behavior('a ^ b ^ c')])
        oracle_path, _ = self.write_oracle()
        review = self.root / 'bad-pin'
        report = review_testbench(PARITY_PROBLEM.encode(), 'TopModule', output, review,
                                  oracle_path=oracle_path, expected_oracle_sha256='0' * 64)
        self.assertEqual(report['status'], 'invalid_reference')
        self.assertFalse((review / 'checked' / 'selftest.cpp').exists())
        self.assert_withheld(report)

    def test_correct_draft_exports_only_checked_copy(self):
        generation, output = self.generated([behavior('a ^ b ^ c'), behavior('a ^ b ^ c')])
        original = (output / 'selftest.cpp').read_bytes()
        oracle_path, expected = self.write_oracle()
        review = self.root / 'review-good'
        report = review_testbench(PARITY_PROBLEM.encode(), 'TopModule', output, review,
                                  oracle_path=oracle_path, expected_oracle_sha256=expected)
        self.assertEqual(report['status'], 'reference_checked')
        self.assertEqual((output / 'selftest.cpp').read_bytes(), original)
        self.assertTrue((review / 'checked' / 'selftest.cpp').is_file())
        self.assert_withheld(report)

    def test_tampered_graph_vectors_cpp_and_receipt_are_invalid_artifacts(self):
        for name in ('expanded_graph.json', 'vectors.json', 'selftest.cpp', 'result.json'):
            with self.subTest(name=name):
                _, output = self.generated([behavior('a ^ b ^ c'), behavior('a ^ b ^ c')])
                target = output / name
                if name == 'expanded_graph.json':
                    value = json.loads(target.read_text()); value['outputs'][0]['when'] = 'false'
                    target.write_text(json.dumps(value), encoding='utf-8')
                elif name == 'vectors.json':
                    value = json.loads(target.read_text()); value[0]['expected']['q'] ^= 1
                    target.write_text(json.dumps(value), encoding='utf-8')
                elif name == 'result.json':
                    value = json.loads(target.read_text()); value['source_sha256'] = 'f' * 64
                    value['receipt_id'] = json_digest({k: v for k, v in value.items() if k != 'receipt_id'})
                    target.write_text(json.dumps(value), encoding='utf-8')
                else:
                    target.write_bytes(target.read_bytes().replace(b'actual_q', b'actual_x', 1))
                oracle_path, expected = self.write_oracle()
                report = assess_generation(PARITY_PROBLEM.encode(), 'TopModule', output,
                                           oracle_path=oracle_path, expected_oracle_sha256=expected)
                self.assertEqual(report['status'], 'invalid_artifact')
                self.assert_withheld(report)

    def test_guarded_generation_never_places_reference_marker_in_model_prompts(self):
        marker = 'PRIVATE-REFERENCE-MARKER-DO-NOT-LEAK'
        table = parity_table(); table['provenance']['review_record'] = marker
        oracle_path, expected = self.write_oracle(table)
        model = Responses([behavior('a ^ b ^ c'), behavior('a ^ b ^ c')])
        result = generate_guarded_behaviors(PARITY_PROBLEM.encode(), 'TopModule', self.root / 'guarded',
                                            model=model, runtime={}, deadline=time.monotonic()+30, max_calls=32,
                                            oracle_path=oracle_path, expected_oracle_sha256=expected)
        self.assertEqual(result['status'], 'reference_checked')
        self.assertTrue(all(marker not in prompt.text and marker not in prompt.system for prompt in model.prompts))
        self.assert_withheld(result)

    def test_guarded_generation_never_overwrites_existing_output(self):
        output = self.root / 'occupied'; output.mkdir(); (output / 'sentinel').write_text('keep')
        model = Responses([])
        with self.assertRaises(FileExistsError):
            generate_guarded_behaviors(PARITY_PROBLEM.encode(), 'TopModule', output, model=model,
                                       runtime={}, deadline=time.monotonic()+30)
        self.assertEqual((output / 'sentinel').read_text(), 'keep')
        self.assertEqual(model.prompts, [])


if __name__ == '__main__':
    unittest.main()
