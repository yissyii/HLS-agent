"""Safety, evidence scope, API/CLI defaults and known-failure regressions."""
import copy
import inspect
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest

from agent.selftest.audit import audit_suite, DEFAULT_AUDIT_MAX_CASES
from agent.selftest.__main__ import main
from agent.selftest.generate import generate_suite
from tools.evaluate_selftest_auditor import expression, task_and_response


class AuditHardening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='audit-hardening-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.serial = 0
        self.suite, self.bound = self.make_suite('gray_decode', 8)

    def make_suite(self, rule, width, source=None):
        self.serial += 1
        task, response = task_and_response(rule, width, source or expression(rule, width))
        suite = self.root / f'suite{self.serial}'
        result = generate_suite(task, suite, response=response)
        self.assertEqual(result['status'], 'generated_unreviewed', result)
        return suite, dict(schema_version=1, suite_id=result['suite_id'], rule_id=rule, width=width,
                           evidence=[dict(source='problem', quote=task.problem)])

    def check(self, value, *, anchor=False, suite=None, raw=None, **kwargs):
        self.serial += 1
        path, out = self.root/f'evidence{self.serial}.json', self.root/f'audit{self.serial}'
        payload = json.dumps(value).encode() if raw is None else raw
        path.write_bytes(payload)
        result = audit_suite(suite or self.suite, out, **{'anchor_file' if anchor else 'binding': path}, **kwargs)
        self.assertEqual(json.loads((out/'audit_result.json').read_text()), result)
        self.assertEqual((out/('supplied_anchors.json' if anchor else 'rule_binding.json')).read_bytes(), payload)
        self.assertTrue(result['manual_review_required'])
        self.assertFalse(result['automatic_repair_allowed'])
        self.assertEqual(result['binding_semantics'], 'not_independently_verified')
        return result

    def assert_incomplete(self, result):
        self.assertEqual(result['status'], 'inconclusive', result)
        self.assertFalse(result['audit_complete'])
        self.assertEqual(result['evidence_scope'], 'none')
        self.assertTrue(result['errors'])

    def test_non_object_attachments(self):
        for anchor in (False, True):
            for value in (None, [], 'text', 1, True, 1.5):
                with self.subTest(anchor=anchor, value=value):
                    result = self.check(value, anchor=anchor)
                    self.assert_incomplete(result)
                    self.assertIn('JSON object', result['errors'][0])

    def test_invalid_json_preserved(self):
        for anchor in (False, True):
            for raw in (b'{', b'\xff', b'{"suite_id":1,"suite_id":2}', b'NaN'):
                with self.subTest(anchor=anchor, raw=raw):
                    self.assert_incomplete(self.check(None, anchor=anchor, raw=raw))

    def test_nested_rule_validation(self):
        for key, value in [('rule_id', []), ('rule_id', {}), ('width', True), ('width', '8'),
                           ('evidence', None), ('evidence', [None]),
                           ('evidence', [{'source': [], 'quote': 'x'}]), ('schema_version', True)]:
            with self.subTest(key=key, value=value):
                bound = copy.deepcopy(self.bound)
                bound[key] = value
                self.assert_incomplete(self.check(bound))

    def anchors(self, rows):
        return dict(schema_version=1, suite_id=self.bound['suite_id'], anchors=rows)

    def test_anchor_structure_validation(self):
        good = dict(inputs=[1], expected=1, evidence=self.bound['evidence'])
        for rows in (None, [], [None], [dict(good, inputs=1)], [dict(good, inputs=[])],
                     [dict(good, inputs=[True])], [dict(good, expected=True)], [dict(good, evidence=None)]):
            with self.subTest(rows=rows):
                self.assert_incomplete(self.check(self.anchors(rows), anchor=True))

    def test_full_domain_is_still_conditional(self):
        result = self.check(self.bound)
        self.assertEqual(result['schema_version'], 2)
        self.assertEqual(result['status'], 'supported')
        self.assertEqual(result['evidence_scope'], 'full_domain_rule')
        self.assertTrue(result['audit_complete'])
        self.assertIn('semantics remain unverified', result['conclusion'])

    def test_explicit_small_budget_stays_sampled(self):
        result = self.check(self.bound, max_cases=4)
        self.assertEqual(result['status'], 'supported')
        self.assertEqual(result['checks'][0]['checked'], 4)
        self.assertEqual(result['evidence_scope'], 'sampled_rule')
        self.assertIn('unchecked inputs may still fail', result['conclusion'])

    def test_anchor_scope_is_not_exhaustive_proof(self):
        result = self.check(self.anchors([dict(inputs=[1], expected=1, evidence=self.bound['evidence'])]), anchor=True)
        self.assertEqual(result['status'], 'supported')
        self.assertEqual(result['evidence_scope'], 'supplied_anchors')
        self.assertIn('expected values', result['conclusion'])

    def test_partial_failure_keeps_conflict_without_complete_scope(self):
        result = self.check(self.anchors([dict(inputs=[1], expected=0, evidence=self.bound['evidence']), None]), anchor=True)
        self.assertEqual(result['status'], 'conflict')
        self.assertFalse(result['audit_complete'])
        self.assertEqual(result['evidence_scope'], 'none')
        self.assertTrue(result['errors'])
        self.assertEqual(len(result['conflicts']), 1)

    def test_wrong_binding_never_authorizes_repair(self):
        suite, bound = self.make_suite('reverse_bits', 8, expression('popcount', 8))
        bound['rule_id'] = 'popcount'
        result = self.check(bound, suite=suite)
        self.assertEqual(result['status'], 'supported')
        self.assertFalse(result['automatic_repair_allowed'])

    def test_default_finds_previous_16_bit_singleton_misses(self):
        for rule in ('gray_decode', 'reverse_bits', 'popcount'):
            with self.subTest(rule=rule):
                core = expression(rule, 16)
                suite, bound = self.make_suite(rule, 16, f'({core}) ^ (1 if x == 3 else 0)')
                before = {p.name:p.read_bytes() for p in suite.iterdir() if p.is_file()}
                result = self.check(bound, suite=suite)
                self.assertEqual(result['status'], 'conflict')
                self.assertEqual(result['checks'][0]['checked'], 65536)
                self.assertEqual(result['checks'][0]['exact_failures'], 1)
                self.assertEqual(result['evidence_scope'], 'full_domain_rule')
                self.assertEqual(before, {p.name:p.read_bytes() for p in suite.iterdir() if p.is_file()})

    def test_cli_defaults_and_invalid_attachment_exit_code(self):
        path = self.root/'cli.json'
        for value, options, expected, budget in [(self.bound, [], 0, 65536),
                                               (self.bound, ['--max-cases', '4'], 0, 4),
                                               (None, [], 2, 65536)]:
            self.serial += 1
            path.write_text(json.dumps(value))
            stream = io.StringIO()
            with redirect_stdout(stream):
                code = main(['audit', str(self.suite), str(self.root/f'cli{self.serial}'), '--binding', str(path)] + options)
            result = json.loads(stream.getvalue())
            self.assertEqual(code, expected)
            self.assertEqual(result['max_cases'], budget)
            self.assertFalse(result['automatic_repair_allowed'])

    def test_generation_budget_unchanged_and_no_evidence_is_incomplete(self):
        self.assertEqual(inspect.signature(generate_suite).parameters['max_cases'].default, 4096)
        self.assertEqual(inspect.signature(audit_suite).parameters['max_cases'].default, DEFAULT_AUDIT_MAX_CASES)
        self.assertEqual(DEFAULT_AUDIT_MAX_CASES, 65536)
        self.assert_incomplete(audit_suite(self.suite, self.root/'no_evidence'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
