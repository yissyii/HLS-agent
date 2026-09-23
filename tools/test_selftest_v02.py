"""Deterministic planner/auditor regression, not a new model-quality benchmark."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from agent.selftest.audit import audit_suite
from agent.selftest.generate import generate_suite, load_suite
from agent.selftest.oracle import expand
from agent.selftest.rules import anchors, property_check, reference
from agent.selftest.sampling import plan_bounded
from agent.selftest.spec import PublicTask, SelftestError
from tools.prepare_selftest_examples import EXAMPLES, response_for
from tools.prepare_selftest_study import HOLDOUT


GRAY_BAD = '(x & 128) ^ ((x >> 1) & 63) ^ ((x >> 2) & 31) ^ ((x >> 3) & 15) ^ ((x >> 4) & 7) ^ ((x >> 5) & 3) ^ ((x >> 6) & 1)'


class BoundedPlanning(unittest.TestCase):
    def setUp(self):
        self.bundle = response_for(EXAMPLES[0])

    def test_overlarge_exhaustive_becomes_bounded_reproducibly(self):
        plan = self.bundle['test_plan']
        plan['sampling'].update(mode='exhaustive', random_cases=0)
        untouched = copy.deepcopy(plan)
        effective, trace = plan_bounded(plan, self.bundle['contract'], 64)
        self.assertEqual((effective, trace), plan_bounded(plan, self.bundle['contract'], 64))
        self.assertEqual(plan, untouched)
        self.assertEqual(trace['effective_mode'], 'boundary_random')
        self.assertEqual(trace['case_count'], 64)
        self.assertEqual(len(expand(effective, self.bundle['contract'], 64)[0]), 64)

    def test_small_domain_is_exhaustive(self):
        bundle = response_for(EXAMPLES[3])
        bundle['test_plan']['sampling'].update(mode='boundary_random', random_cases=2)
        _, trace = plan_bounded(bundle['test_plan'], bundle['contract'])
        self.assertEqual(trace['case_count'], 256)
        self.assertEqual(trace['effective_mode'], 'exhaustive')

    def test_explicit_cases_retained_and_budget_failure_not_silent(self):
        plan = self.bundle['test_plan']
        plan['explicit_cases'] = [dict(inputs=[i, 7], rule_ids=['R1']) for i in range(5)]
        effective, trace = plan_bounded(plan, self.bundle['contract'], 8)
        self.assertEqual(effective['explicit_cases'][:5], plan['explicit_cases'])
        self.assertEqual(trace['explicit_unique_count'], 5)
        with self.assertRaisesRegex(SelftestError, 'Explicit cases exceed'):
            plan_bounded(plan, self.bundle['contract'], 4)

    def test_bool_normalization_only_for_bool_type(self):
        bundle = response_for(EXAMPLES[2])
        bundle['test_plan']['explicit_cases'] = [dict(inputs=[0, 15, True], rule_ids=['R1'])]
        effective, trace = plan_bounded(bundle['test_plan'], bundle['contract'])
        self.assertEqual(effective['explicit_cases'][0]['inputs'], [0, 15, 1])
        self.assertEqual(len(trace['normalized_bool_inputs']), 1)
        self.assertIs(bundle['test_plan']['explicit_cases'][0]['inputs'][2], True)
        for invalid in (True, 0.5, '1', 256):
            bundle['test_plan']['explicit_cases'][0]['inputs'][0] = invalid
            with self.assertRaises(SelftestError):
                plan_bounded(bundle['test_plan'], bundle['contract'])

    def test_tiny_boundary_budget_and_zero_random(self):
        self.bundle['test_plan']['sampling']['random_cases'] = 0
        _, trace = plan_bounded(self.bundle['test_plan'], self.bundle['contract'], 3)
        self.assertLessEqual(trace['case_count'], 3)
        self.assertIn('boundary_product_subsampled_with_fixed_seed', trace['adjustments'])

    def test_large_random_request_is_recorded_and_capped(self):
        self.bundle['test_plan']['sampling']['random_cases'] = 65536
        _, trace = plan_bounded(self.bundle['test_plan'], self.bundle['contract'], 10)
        self.assertLessEqual(trace['case_count'], 10)
        self.assertEqual(trace['requested_sampling']['random_cases'], 65536)

    def test_duplicate_explicit_cases_merge(self):
        plan = self.bundle['test_plan']
        plan['explicit_cases'] = [dict(inputs=[0, 0], rule_ids=['R1']) for _ in range(9)]
        _, trace = plan_bounded(plan, self.bundle['contract'], 1)
        self.assertEqual(trace['case_count'], 1)
        self.assertEqual(trace['explicit_unique_count'], 1)

    def test_unknown_refs_and_invalid_sampling_rejected(self):
        for mode in ('bogus', 'explicit'):
            self.bundle['test_plan']['sampling']['mode'] = mode
            with self.assertRaises(SelftestError):
                plan_bounded(self.bundle['test_plan'], self.bundle['contract'])
        self.bundle['test_plan']['sampling']['mode'] = 'boundary_random'
        self.bundle['test_plan']['oracle']['rule_ids'] = ['MISSING']
        with self.assertRaises(SelftestError):
            plan_bounded(self.bundle['test_plan'], self.bundle['contract'])


class AuditRegression(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='selftest-v02-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def suite(self, rule='gray_decode', expression=None, name='suite'):
        item = next(x for x in HOLDOUT if x['id'] == {'gray_decode':'gray_decode8', 'reverse_bits':'reverse_bits8', 'popcount':'count_bits8'}[rule])
        bundle = response_for(item)
        if expression is not None:
            bundle['test_plan']['oracle']['expression'] = expression
        task = PublicTask(item['problem'], f'#include <stdint.h>\nuint8_t {item["id"]}(uint8_t x);\n')
        result = generate_suite(task, self.root/name, response=bundle, planning_policy='bounded-v2')
        self.assertEqual(result['status'], 'generated_unreviewed', result)
        binding = dict(schema_version=1, suite_id=result['suite_id'], rule_id=rule, width=8,
                       evidence=[dict(source='problem', quote=item['problem'])])
        path = self.root/(name+'_binding.json')
        path.write_text(json.dumps(binding), encoding='utf-8')
        return self.root/name, path

    def test_three_correct_rules_supported_not_proven(self):
        for rule in ('gray_decode', 'reverse_bits', 'popcount'):
            suite, binding = self.suite(rule, name=rule)
            result = audit_suite(suite, self.root/(rule+'_audit'), binding=binding)
            self.assertEqual(result['status'], 'supported', result)
            self.assertEqual(result['binding_semantics'], 'not_independently_verified')
            self.assertTrue(result['checks'][0]['exhaustive'])
            self.assertFalse(result['candidate_visible'])

    def test_original_gray_failure_has_x1_witness_and_254_mismatches(self):
        suite, binding = self.suite(expression=GRAY_BAD)
        before = {p.name:p.read_bytes() for p in suite.iterdir() if p.is_file()}
        result = audit_suite(suite, self.root/'audit', binding=binding)
        self.assertEqual(result['status'], 'conflict')
        self.assertEqual(result['checks'][0]['exact_failures'], 254)
        self.assertEqual(result['conflicts'][0], dict(kind='bound_rule', inputs=[1], expected=1, actual=0))
        self.assertEqual(before, {p.name:p.read_bytes() for p in suite.iterdir() if p.is_file()})

    def test_involution_alone_does_not_certify_identity(self):
        self.assertIsNone(property_check('reverse_bits', 8, 27, 27, lambda x:x))
        suite, binding = self.suite('reverse_bits', 'x')
        result = audit_suite(suite, self.root/'audit', binding=binding)
        self.assertEqual(result['status'], 'conflict')
        self.assertEqual(result['checks'][0]['property_failures'], 0)
        self.assertGreater(result['checks'][0]['exact_failures'], 0)

    def test_rare_case_is_found_with_exhaustive_audit(self):
        item = HOLDOUT[0]
        suite, binding = self.suite('popcount', f'0 if x==181 else ({item["oracle"]})')
        result = audit_suite(suite, self.root/'audit', binding=binding)
        self.assertEqual(result['checks'][0]['exact_failures'], 1)

    def test_missing_evidence_is_inconclusive(self):
        suite, _ = self.suite()
        result = audit_suite(suite, self.root/'audit')
        self.assertEqual(result['status'], 'inconclusive')

    def test_foreign_binding_and_invalid_quote_rejected(self):
        suite, binding = self.suite()
        original = json.loads(binding.read_text())
        for i in range(3):
            value = copy.deepcopy(original)
            if i == 0: value['suite_id'] = 'different'
            if i == 1: value['evidence'][0]['quote'] = 'invented'
            if i == 2: value['width'] = 4
            binding.write_text(json.dumps(value))
            result = audit_suite(suite, self.root/f'audit{i}', binding=binding)
            self.assertEqual(result['status'], 'inconclusive')

    def test_supplied_anchor_is_conditional_evidence(self):
        suite, binding = self.suite(expression=GRAY_BAD)
        value = json.loads(binding.read_text())
        anchors_file = self.root/'anchors.json'
        anchors_file.write_text(json.dumps(dict(schema_version=1, suite_id=value['suite_id'],
            anchors=[dict(inputs=[1], expected=1, evidence=value['evidence'])])))
        result = audit_suite(suite, self.root/'audit', anchor_file=anchors_file)
        self.assertEqual(result['status'], 'conflict')

    def test_exclusive_output(self):
        suite, binding = self.suite()
        audit_suite(suite, self.root/'audit', binding=binding)
        with self.assertRaises(FileExistsError):
            audit_suite(suite, self.root/'audit', binding=binding)

    def test_trace_tampering_rejected(self):
        suite, _ = self.suite()
        (suite/'planning.json').write_text('{}')
        with self.assertRaises(SelftestError):
            load_suite(suite)

    def test_large_effective_plan_roundtrips(self):
        item = EXAMPLES[0]
        task = PublicTask(item['problem'], 'uint8_t add8(uint8_t a, uint8_t b);')
        result = generate_suite(task, self.root/'large', response=response_for(item),
                                max_cases=65536, planning_policy='bounded-v2')
        self.assertEqual(result['status'], 'generated_unreviewed', result)
        self.assertEqual(len(load_suite(self.root/'large')[3]), 65536)

    def test_width_families_against_separate_identities(self):
        for width in (1, 2, 4, 8):
            for x in range(1 << width):
                gray = 0
                for shift in range(width): gray ^= x >> shift
                self.assertEqual(reference('gray_decode', width, x), gray)
                rev = 0
                for i in range(width): rev |= ((x >> i) & 1) << (width-1-i)
                self.assertEqual(reference('reverse_bits', width, x), rev)
                count, n = 0, x
                while n: n &= n-1; count += 1
                self.assertEqual(reference('popcount', width, x), count)
            for rule in ('gray_decode', 'reverse_bits', 'popcount'):
                for x, y in anchors(rule, width):
                    self.assertEqual(reference(rule, width, x), y)


if __name__ == '__main__':
    unittest.main()
