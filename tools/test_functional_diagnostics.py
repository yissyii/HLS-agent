"""Functional facts, release isolation and model-facing repair contracts."""
import json
import time
import unittest
from unittest.mock import patch

from evaluation.functional import extract_functional, format_functional, fit_feedback, HEADER
from evaluation.diagnostics import extract_diagnostics
from evaluation.validator import HLSValidator
from evaluation.task_io import load_task
from agent.core.contracts import Candidate
from agent.feedback.diagnostics import classify
from agent.context.builder import build
from rag.query import query_plan
from tools.test_agent_contract import AgentContract


class FunctionalParsing(unittest.TestCase):
    def extract(self, text, timed_out=False):
        return extract_diagnostics('csim', {'timed_out': timed_out, 'exit_code': 1}, text)

    def test_mismatch_preserves_values_without_inventing_inputs(self):
        result = self.extract('Mismatch at cycle 48: expected 1, got 0\nTest Failed: 6 mismatches detected out of 200 cases.')
        self.assertEqual(result['category'], 'functional_or_runtime_error')
        report = result['functional_diagnostics']
        first = report['events'][0]
        self.assertEqual(first['observed'], dict(cycle=48, expected='1', actual='0'))
        self.assertNotIn('inputs', first['observed'])
        self.assertEqual(first['log_line'], 1)
        self.assertEqual(report['events'][1]['observed']['mismatches_reported'], 6)
        self.assertNotIn('Mismatch', result['compiler_text'])

    def test_runtime_is_not_compiler_and_timeout_is_not_deadlock(self):
        for text in ('kernel.cpp:7:9: runtime error: division by zero', 'Segmentation fault (core dumped)',
                     '==12==ERROR: AddressSanitizer: heap-buffer-overflow'):
            result = self.extract(text)
            self.assertEqual(result['category'], 'functional_or_runtime_error')
            self.assertEqual(result['functional_diagnostics']['events'][0]['failure_kind'], 'runtime_exception')
        result = self.extract('still running', timed_out=True)
        self.assertEqual(result['category'], 'tool_timeout')
        self.assertEqual(result['functional_diagnostics']['events'], [])

    def test_assertion_and_nonzero_exit_are_distinct(self):
        report = self.extract("csim: tb.cpp:8: main: Assertion `x == 7' failed.\n@E Simulation failed: Function 'main' returns nonzero value '1'.")['functional_diagnostics']
        self.assertEqual([e['failure_kind'] for e in report['events']], ['assertion_failure', 'nonzero_exit'])
        self.assertEqual(report['events'][1]['observed']['program_exit_code'], 1)
        self.assertEqual(report['tool_exit_code'], 1)

    def test_explicit_json_trace_allowlist_and_bounds(self):
        data = dict(kind='output_mismatch', cycle=3, inputs={'a': 1}, reset=False,
                    history=[{'cycle': 2, 'inputs': {'a': 0}}], expected='x'*600, actual='0',
                    hidden_extra='never released')
        report = self.extract('ZCOMP_FUNCTIONAL '+json.dumps(data))['functional_diagnostics']
        event = report['events'][0]
        self.assertEqual(event['observed']['history'][0]['cycle'], 2)
        self.assertEqual(len(event['observed']['expected']), 200)
        self.assertTrue(event['truncated'])
        self.assertNotIn('hidden_extra', event['observed'])
        for bad in ('{bad}', '{"kind":"output_mismatch","actual":NaN}', '{"kind":"other"}'):
            self.assertEqual(self.extract('ZCOMP_FUNCTIONAL '+bad)['functional_diagnostics']['events'], [])

    def test_dedup_and_whole_event_budget(self):
        lines = ['Mismatch at cycle 0: expected 1, got 0']*2
        lines += [f'Mismatch at cycle {i}: expected 1, got 0' for i in range(1, 35)]
        report = self.extract('\n'.join(lines))['functional_diagnostics']
        self.assertEqual(report['observed_events'], 36)
        self.assertEqual(report['unique_events'], 35)
        self.assertEqual(len(report['events']), 20)
        rendered = format_functional(report)
        payload = json.loads(rendered.split('\n', 1)[1])
        self.assertEqual(len(payload['events']), 3)
        self.assertEqual(payload['omitted_unique_events'], 32)
        self.assertLessEqual(len(rendered), 2800)

    def test_compiler_block_does_not_swallow_functional_line(self):
        text = 'kernel.cpp:1:2: error: unknown name\nMismatch at cycle 9: expected SECRET, got 0'
        result = self.extract(text)
        self.assertEqual(result['category'], 'compile_error')
        self.assertNotIn('SECRET', result['compiler_text'])

    def test_budget_preserves_whole_structured_events(self):
        report = self.extract('\n'.join(f'Mismatch at cycle {i}: expected 1, got 0' for i in range(8)))['functional_diagnostics']
        text = format_functional(report)
        for cap in (128, 256, 600, 900):
            fitted = fit_feedback(text, cap)
            self.assertLessEqual(len(fitted), cap)
            if fitted.startswith(HEADER):
                payload = json.loads(fitted[len(HEADER):])
                self.assertTrue(all('expected' in e['observed'] and 'actual' in e['observed'] for e in payload['events']))

    def test_runtime_query_uses_observed_message_not_counterexample(self):
        report = self.extract('kernel.cpp:7:9: runtime error: division by zero')['functional_diagnostics']
        query, plan = query_plan('Implement division.', format_functional(report),
                                 'functional_or_runtime_error', 'functional_diagnostics', 1600)
        self.assertIsNone(plan['skip_reason'])
        self.assertIn('division by zero', query)
        self.assertNotIn('schema_version', query)


class FunctionalRelease(unittest.TestCase):
    setUpClass = classmethod(AgentContract.setUpClass.__func__)
    tearDownClass = classmethod(AgentContract.tearDownClass.__func__)
    public_task = AgentContract.public_task
    solve = AgentContract.solve
    def setUp(self): AgentContract.setUp(self)
    def tearDown(self): AgentContract.tearDown(self)

    def test_manifest_release_prompt_query_and_checks(self):
        self.public_task(feedback='functional_diagnostics')
        task = load_task(self.problem, self.manifest)
        candidate = Candidate(0, 'int kernel(int a){return a;}', self.directory/'c0/candidate.cpp', None)
        extracted = extract_diagnostics('csim', {'timed_out': False, 'exit_code': 1},
                                       'Mismatch at cycle 48: expected SECRET, got 0\nPRIVATE_UNRELATED_LOG')
        outcome = dict(extracted, status='failed')
        validator = HLSValidator()
        with patch('evaluation.validator.validate_stage', return_value=outcome):
            for policy in ('category_only', 'compiler_diagnostics', 'functional_diagnostics', 'public_diagnostics'):
                task.manifest['feedback_policy'] = policy
                result = validator.check(candidate, task, self.config, 'csim', time.monotonic()+10)
                prompt = build(task, self.config, self.policy, candidate, classify(result))
                if policy in ('category_only', 'compiler_diagnostics'):
                    self.assertNotIn('SECRET', prompt.text)
                elif policy == 'functional_diagnostics':
                    self.assertIn('SECRET', prompt.text)
                    self.assertNotIn('PRIVATE_UNRELATED_LOG', prompt.text)
                    self.assertEqual(result.checks['run'], 'failed')
                    _, plan = query_plan(task.problem.decode(), result.feedback_text, result.outcome['category'], policy, 1600)
                    self.assertEqual(plan['skip_reason'], 'functional_feedback_without_manual_signal')
                else:
                    self.assertIn('PRIVATE_UNRELATED_LOG', prompt.text)

    def test_missing_functional_evidence_does_not_fall_back_to_raw_tail(self):
        self.public_task(feedback='functional_diagnostics')
        task = load_task(self.problem, self.manifest)
        candidate = Candidate(0, 'int kernel(int a){return a;}', self.directory/'c0/candidate.cpp', None)
        outcome = dict(status='failed', category='functional_or_runtime_error', diagnostic_tail='SECRET')
        with patch('evaluation.validator.validate_stage', return_value=outcome):
            result = HLSValidator().check(candidate, task, self.config, 'csim', time.monotonic()+10)
        self.assertEqual(result.feedback_text, 'functional_or_runtime_error')

    def test_loopback_repair_request_contains_structured_facts(self):
        self.public_task(feedback='functional_diagnostics')
        type(self).response_mode = 'repair'
        extracted = extract_diagnostics('csim', {'timed_out': False, 'exit_code': 1},
                                       'Mismatch at cycle 5: expected 7, got 0\nUNRELATED_PRIVATE_LOG')
        with patch.object(HLSValidator, 'preflight'), patch('evaluation.validator.validate_stage',
             side_effect=[dict(extracted, status='failed'), {'status':'passed'}, {'status':'passed'}]):
            code, result = self.solve(model=None, validator=HLSValidator())
        self.assertEqual((code, result['status']), (0, 'passed'))
        self.assertEqual(len(self.requests), 2)
        repair = self.requests[1]['messages'][1]['content']
        self.assertIn('"expected": "7"', repair)
        self.assertIn('"cycle": 5', repair)
        self.assertNotIn('UNRELATED_PRIVATE_LOG', repair)
        self.assertEqual(self.requests[0]['messages'][0], self.requests[1]['messages'][0])


if __name__ == '__main__': unittest.main()
