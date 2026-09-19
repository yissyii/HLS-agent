"""Agent behavior tests using synthetic tasks, fake validators and loopback HTTP."""
import argparse
import copy
import contextlib
import io
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.core.contracts import ValidationResult, digest, json_digest
from agent.core.policy import load_policy
from agent.interface.entry import run
from evaluation.task_io import load_task
from evaluation.validator import HLSValidator
from serve.baseline_entry import config_digest
from serve.inference import Failure, load_config
from serve import paired_entry
from evaluation import single_task


class FakeModel:
    def __init__(self, responses=None):
        self.responses = responses or ['int kernel(int a) { return a + 1; }\n']
        self.prompts = []

    def generate(self, prompt, runtime, directory, deadline):
        self.prompts.append(prompt)
        text = self.responses[min(len(self.prompts) - 1, len(self.responses) - 1)]
        (directory / 'response.txt').write_bytes(text.encode())
        return {'status': 'passed', 'requests': 1, 'usage': {'total_tokens': 10},
                'request_outcome_unknown': False, 'model': runtime['model']}


class FakeValidator:
    def __init__(self, failures=None, preflight_error=None, mismatched_hash=False):
        self.failures = failures or {}
        self.calls = []
        self.preflight_error = preflight_error
        self.mismatched_hash = mismatched_hash

    def preflight(self, task, runtime, directory):
        if self.preflight_error:
            raise Failure(self.preflight_error, 'Synthetic setup failure')

    def check(self, candidate, task, runtime, stage, deadline):
        self.calls.append((candidate.candidate_id, stage))
        category = self.failures.get((candidate.candidate_id, stage))
        outcome = {'status': 'failed' if category else 'passed', 'elapsed_seconds': .01}
        if category:
            outcome['category'] = category
        if stage == 'synthesis':
            checks = {'synthesize': outcome['status']}
        elif not category:
            checks = {'parse': 'passed', 'compile': 'passed', 'run': 'passed'}
        elif category == 'functional_or_runtime_error':
            checks = {'parse': 'passed', 'compile': 'passed', 'run': 'failed'}
        else:
            checks = {'parse': 'unknown', 'compile': 'failed', 'run': 'not_run'}
        return ValidationResult('wrong' if self.mismatched_hash else candidate.sha256,
                                task.fingerprint, json_digest(runtime['hls']), stage, outcome,
                                checks, 'error: ' + str(category))


class AgentContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'output').mkdir(exist_ok=True)
        cls.requests = []
        cls.response_mode = 'stop'
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                cls.requests.append(payload)
                mode = cls.response_mode
                if mode == 'slow':
                    time.sleep(1.5)
                data = json.dumps({'choices': [{'message': {'content': 'int kernel(int a) { return a+1; }\n'},
                                                'finish_reason': 'length' if mode == 'length' else 'stop'}],
                                   'usage': {'prompt_tokens': 20, 'completion_tokens': 10}}).encode()
                try:
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agent_test_', dir=ROOT / 'output')
        self.directory = Path(self.temp.name)
        self.problem = self.directory / 'problem.txt'
        self.problem.write_bytes(b'Implement int kernel(int a), returning a + 1. Inputs are 0..20.\r\n')
        self.config = json.loads((ROOT / 'serve/runtime.json').read_text())
        self.config['model']['base_url'] = f'http://127.0.0.1:{self.server.server_port}/v1'
        self.config['hls']['total_timeout_seconds'] = 15
        self.policy = load_policy()
        self.policy.update(validation_reserve_seconds=0, cleanup_reserve_seconds=0)
        self.model = FakeModel()
        self.validator = FakeValidator()
        self.manifest = None
        type(self).requests = []
        type(self).response_mode = 'stop'

    def tearDown(self):
        self.temp.cleanup()

    def public_task(self, testbench=True, feedback='category_only'):
        (self.directory / 'tb.cpp').write_text('int main() { return 0; }\n')
        (self.directory / 'api.h').write_text('int kernel(int a);\n')
        data = dict(id='synthetic', top_function='kernel', problem_file='problem.txt',
                    testbench_files=['tb.cpp'] if testbench else [], support_files=['api.h'],
                    model_visible=['api.h'], feedback_policy=feedback)
        self.manifest = self.directory / 'task.json'
        self.manifest.write_text(json.dumps(data))
        return self.manifest

    def solve(self, name='agent', **kwargs):
        values = dict(config=self.config, policy=self.policy, manifest=self.manifest,
                      model=self.model, validator=self.validator, run_id='test-run')
        values.update(kwargs)
        return run(self.problem, self.directory / name, **values)

    def test_problem_only_receipt_and_no_overwrite(self):
        code, result = self.solve()
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'generated_unvalidated')
        self.assertEqual(result['validation_status'], 'not_run')
        self.assertEqual(result['generation_requests'], 1)
        self.assertEqual(result['tool_calls'], 0)
        self.assertEqual(result['config_sha256'], config_digest(self.config))
        self.assertEqual(result['problem_sha256'], digest(self.problem.read_bytes()))
        self.assertEqual((self.directory / 'agent/problem.txt').read_bytes(), self.problem.read_bytes())
        with self.assertRaises(FileExistsError):
            self.solve()

    def test_first_candidate_pass_stops(self):
        self.public_task()
        code, result = self.solve()
        self.assertEqual((code, result['status'], result['tool_calls']), (0, 'passed', 2))
        self.assertEqual(len(self.model.prompts), 1)

    def test_compile_failure_repair_then_full_revalidation(self):
        self.public_task()
        self.model = FakeModel(['int kernel(int a) { return bad; }', 'int kernel(int a) { return a+1; }'])
        self.validator = FakeValidator({(0, 'csim'): 'compile_error'})
        code, result = self.solve()
        self.assertEqual(code, 0)
        self.assertEqual(result['selected_candidate'], 1)
        self.assertEqual(result['generation_requests'], 2)
        self.assertEqual(result['repair_attempts_used'], 1)
        self.assertEqual(self.validator.calls, [(0, 'csim'), (1, 'csim'), (1, 'synthesis')])
        self.assertIn('int kernel(int a);', self.model.prompts[0].text)
        self.assertNotIn('int main()', self.model.prompts[0].text)

    def test_functional_failure_is_repaired(self):
        self.public_task()
        self.model = FakeModel(['int kernel(int a) { return a; }', 'int kernel(int a) { return a+1; }'])
        self.validator = FakeValidator({(0, 'csim'): 'functional_or_runtime_error'})
        _, result = self.solve()
        self.assertEqual(result['status'], 'passed')

    def test_synthesis_failure_restarts_csim(self):
        self.public_task()
        self.model = FakeModel(['int kernel(int a){return a+1;}', 'int kernel(int a){int b=a+1;return b;}'])
        self.validator = FakeValidator({(0, 'synthesis'): 'synthesis_error'})
        _, result = self.solve()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(self.validator.calls, [(0, 'csim'), (0, 'synthesis'), (1, 'csim'), (1, 'synthesis')])

    def test_regression_selects_earlier_evidence(self):
        self.public_task()
        self.policy['max_repairs'] = 1
        self.model = FakeModel(['int kernel(int a){return a+1;}', 'bad new source'])
        self.validator = FakeValidator({(0, 'synthesis'): 'synthesis_error', (1, 'csim'): 'compile_error'})
        code, result = self.solve()
        self.assertEqual(code, 0)  # Delivered a candidate, not a successful evaluation.
        self.assertEqual(result['validation_status'], 'failed')
        self.assertEqual(result['selected_candidate'], 0)
        self.assertEqual(result['checks']['run'], 'passed')
        self.assertEqual(result['checks']['synthesize'], 'failed')
        self.assertEqual((self.directory / 'agent/candidate.cpp').read_text(), self.model.responses[0])

    def test_duplicate_does_not_repeat_tools(self):
        self.public_task()
        self.validator = FakeValidator({(0, 'csim'): 'compile_error'})
        _, result = self.solve()
        self.assertEqual(result['stop_reason'], 'repeated_candidate')
        self.assertEqual(result['generation_requests'], 2)
        self.assertEqual(result['tool_calls'], 1)

    def test_stagnant_different_source_stops(self):
        self.public_task()
        self.model = FakeModel(['bad 0', 'bad 1', 'bad 2'])
        self.validator = FakeValidator({(i, 'csim'): 'compile_error' for i in range(3)})
        _, result = self.solve()
        self.assertEqual(result['stop_reason'], 'stagnation')
        self.assertEqual(len(self.model.prompts), 2)

    def test_infrastructure_never_repairs(self):
        self.public_task()
        for category in ('license_error', 'tool_timeout', 'environment_or_dependency_error'):
            self.validator = FakeValidator({(0, 'csim'): category})
            code, result = self.solve(category)
            self.assertEqual(code, 1)
            self.assertEqual(result['generation_requests'], 1)
            self.assertEqual(result['category'], category)

    def test_preflight_failure_makes_no_model_call(self):
        self.public_task()
        self.validator = FakeValidator(preflight_error='environment_error')
        code, result = self.solve()
        self.assertEqual(code, 1)
        self.assertEqual(self.model.prompts, [])

    def test_synthesis_only_never_claims_functional_success(self):
        self.public_task(testbench=False)
        _, result = self.solve()
        self.assertEqual(result['checks']['run'], 'not_run')
        self.assertEqual(result['validation_scope'], 'synthesis_only')
        self.assertEqual(self.validator.calls, [(0, 'synthesis')])

    def test_evidence_must_match_source_hash(self):
        self.public_task()
        self.validator = FakeValidator(mismatched_hash=True)
        code, result = self.solve()
        self.assertEqual(code, 1)
        self.assertEqual(result['category'], 'evidence_mismatch')

    def test_context_overflow_refuses_before_model(self):
        self.problem.write_text('x' * (self.config['model']['context_tokens'] + 1))
        code, result = self.solve()
        self.assertEqual(code, 1)
        self.assertEqual(result['stop_reason'], 'context_budget_exceeded')
        self.assertEqual(self.model.prompts, [])

    def test_insufficient_budget_stops_before_repair(self):
        self.public_task()
        self.policy['validation_reserve_seconds'] = 100
        self.validator = FakeValidator({(0, 'csim'): 'compile_error'})
        _, result = self.solve()
        self.assertEqual(result['stop_reason'], 'insufficient_validation_budget')
        self.assertEqual(result['generation_requests'], 1)

    def test_manifest_escape_and_problem_mismatch_rejected(self):
        self.public_task()
        data = json.loads(self.manifest.read_text())
        data['support_files'] = ['../private.h']
        self.manifest.write_text(json.dumps(data))
        code, result = self.solve('escape')
        self.assertEqual(code, 1)
        self.assertEqual(self.model.prompts, [])
        self.public_task()
        other = self.directory / 'other.txt'
        other.write_text('different')
        with self.assertRaises(Failure):
            load_task(other, self.manifest)

    def test_frozen_config_ignores_environment_overrides(self):
        config_path = self.directory / 'config.json'
        config_path.write_text(json.dumps(self.config))
        with patch.dict(os.environ, {'LLM_MODEL': 'wrong', 'LLM_MAX_TOKENS': '999999'}):
            frozen = load_config(config_path, frozen=True, allow_external=True)
            code, result = self.solve(config=config_path)
        self.assertEqual(frozen['model']['name'], self.config['model']['name'])
        self.assertEqual(result['config_sha256'], config_digest(self.config))
        self.assertEqual(code, 0)

    def test_validator_visibility_and_isolated_workspaces(self):
        from agent.core.contracts import Candidate
        self.public_task()
        task = load_task(self.problem, self.manifest)
        validator = HLSValidator()
        secret = 'SENSITIVE_REFERENCE_OUTPUT'
        code_hint = 'kernel.cpp:1:1: error: no matching function for call'
        def fake_stage(stage, work, manifest, settings, cpu_only, budget):
            return {'status': 'failed', 'category': 'functional_or_runtime_error',
                    'diagnostic_tail': secret, 'compiler_text': code_hint}
        works = []
        with patch('evaluation.validator.validate_stage', side_effect=fake_stage):
            for i in range(2):
                path = self.directory / f'candidate_{i}' / 'candidate.cpp'
                candidate = Candidate(i, 'int kernel(int a){return a;}', path, None)
                result = validator.check(candidate, task, self.config, 'csim', time.monotonic() + 3)
                works.append(path.parent / 'work')
                self.assertNotIn(secret, result.feedback_text)
            # compiler_diagnostics releases code errors, holds back the functional oracle.
            task.manifest['feedback_policy'] = 'compiler_diagnostics'
            result = validator.check(candidate, task, self.config, 'csim', time.monotonic() + 3)
            self.assertIn(code_hint, result.feedback_text)
            self.assertNotIn(secret, result.feedback_text)
            task.manifest['feedback_policy'] = 'public_diagnostics'
            result = validator.check(candidate, task, self.config, 'csim', time.monotonic() + 3)
            self.assertIn(secret, result.feedback_text)
        self.assertNotEqual(works[0], works[1])
        self.assertTrue(all((w / 'input/kernel.cpp').is_file() for w in works))

    def test_loopback_worker_request_and_truncation(self):
        code, result = self.solve(model=None)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.requests), 1)
        saved = json.loads((self.directory / 'agent/candidates/000/request.json').read_text())
        self.assertEqual(saved, self.requests[0])
        self.assertEqual(result['generation_requests'], 1)
        type(self).response_mode = 'length'
        code, result = self.solve('truncated', model=None)
        self.assertEqual(code, 1)
        self.assertEqual(result['category'], 'generation_incomplete')
        self.assertFalse((self.directory / 'truncated/candidate.cpp').exists())
        self.assertTrue((self.directory / 'truncated/candidates/000/response.txt').exists())

    def test_worker_hard_timeout_and_attempt_accounting(self):
        type(self).response_mode = 'slow'
        self.config['model']['timeout_seconds'] = .5
        started = time.monotonic()
        code, result = self.solve(model=None)
        self.assertEqual(code, 1)
        self.assertEqual(result['generation_requests'], 1)
        self.assertIn(result['category'], {'generation_timeout', 'api_network_or_timeout'})
        self.assertLess(time.monotonic() - started, 3)

    def test_real_cli_and_pairing_use_independent_requests(self):
        self.config['model']['context_note'] = '测试 UTF-8 配置快照'
        config_path = self.directory / 'config.json'
        config_path.write_text(json.dumps(self.config, ensure_ascii=False), encoding='utf-8')
        argv = ['paired', str(self.problem), str(self.directory / 'pair'), '--config', str(config_path)]
        # On Windows the standard agent entry is invoked with the current Python.
        # POSIX invokes the actual run.sh through bash.
        with patch.object(sys, 'argv', argv):
            self.assertEqual(paired_entry.main(), 0)
        round_output = self.directory / 'pair/attempt_000/result'
        pair = json.loads((round_output / 'pair.json').read_text())
        agent = json.loads((round_output / 'agent/result.json').read_text(encoding='utf-8'))
        baseline = json.loads((round_output / 'baseline/result.json').read_text(encoding='utf-8'))
        self.assertTrue(pair['pairing_verified'])
        self.assertEqual(agent['run_id'], baseline['run_id'])
        self.assertEqual(agent['config_sha256'], baseline['config_sha256'])
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[0]['messages'][0]['content'], self.problem.read_bytes().decode())
        self.assertNotEqual(self.requests[0]['messages'], self.requests[1]['messages'])

    def test_legacy_entry_uses_controller_and_validation_exit_code(self):
        self.public_task()
        source = self.directory / 'existing.cpp'
        source.write_text('int kernel(int a){return a+1;}')
        args = argparse.Namespace(manifest=str(self.manifest), source=str(source),
                                  config=None, cpu_only=True, repair_attempts=0)
        with patch.object(single_task, 'load_config', return_value=self.config), \
             patch('agent.interface.entry.HLSValidator', return_value=self.validator):
            self.assertEqual(single_task.evaluate(args), 0)
        self.assertEqual(len(self.model.prompts), 0)
        self.assertEqual(len(self.validator.calls), 2)

    def test_skills_disabled_or_require_validated_pack(self):
        self.policy['skills_enabled'] = True
        code, result = self.solve(skills_dir=self.directory / 'missing_rules')
        self.assertEqual(code, 1)
        self.assertEqual(result['category'], 'skill_error')
        self.assertEqual(self.model.prompts, [])

    def test_msys_permission_failure_is_not_code_error(self):
        from evaluation.diagnostics import classify_category
        log = "cat.exe: *** fatal error - couldn't create signal pipe, Win32 error 5"
        self.assertEqual(classify_category('csim', {'timed_out': False}, log), 'environment_or_dependency_error')

    def test_enabled_rules_are_read_only_and_only_used_on_repair(self):
        self.public_task(feedback='public_diagnostics')
        self.policy['skills_enabled'] = True
        rules = self.directory / 'rules'
        rules.mkdir()
        # Synthetic rule tests the retrieval contract, not a claimed competition skill.
        rule = dict(id='synthetic-rule', version='test', tool_version='2025.2', categories=['compile_error'],
                    keywords=['compile_error'], guidance='SYNTHETIC_RULE_MARKER', preconditions='fixture only',
                    failure_modes='not for real tasks', source='synthetic fixture', license='test fixture',
                    validation={'held_out_passed': True, 'split': 'independent_development'})
        path = rules / 'rule.json'
        path.write_text(json.dumps(rule))
        original = path.read_bytes()
        self.model = FakeModel(['bad', 'int kernel(int a){return a+1;}'])
        self.validator = FakeValidator({(0, 'csim'): 'compile_error'})
        code, result = self.solve(skills_dir=rules)
        self.assertEqual(code, 0)
        self.assertTrue(result['skills_used'])
        self.assertNotIn('SYNTHETIC_RULE_MARKER', self.model.prompts[0].text)
        self.assertIn('SYNTHETIC_RULE_MARKER', self.model.prompts[1].text)
        self.assertEqual(path.read_bytes(), original)

    def test_pair_forwards_frozen_public_inputs(self):
        self.public_task()
        config_path = self.directory / 'config.json'
        config_path.write_text(json.dumps(self.config))
        output = self.directory / 'pair_public'
        def process(command, work, environment, log, timeout):
            config = command[command.index('--config') + 1]
            manifest = command[command.index('--task-manifest') + 1]
            policy = command[command.index('--policy') + 1]
            round_output = output / 'attempt_000/result'
            code, receipt = run(round_output / 'problem.txt', round_output / 'agent', config=config,
                                manifest=manifest, policy=policy, run_id=command[command.index('--run-id') + 1],
                                model=FakeModel(), validator=FakeValidator())
            self.assertTrue(Path(manifest).is_relative_to(output))
            return dict(exit_code=code, timed_out=False, elapsed_seconds=0, log='agent.log')
        argv = ['paired', str(self.problem), str(output), '--config', str(config_path), '--task-manifest', str(self.manifest)]
        with patch.object(sys, 'argv', argv), patch.object(paired_entry, 'run_process', side_effect=process), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(paired_entry.main(), 0)
        result = json.loads((output / 'attempt_000/result/agent/result.json').read_text())
        self.assertEqual(result['task_sha256'], load_task(self.problem, self.manifest).fingerprint)
        self.assertEqual(result['status'], 'passed')

    def test_baseline_failure_does_not_skip_agent(self):
        config_path = self.directory / 'config.json'
        config_path.write_text(json.dumps(self.config))
        type(self).response_mode = 'length'
        output = self.directory / 'pair_failed'
        argv = ['paired', str(self.problem), str(output), '--config', str(config_path)]
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(paired_entry.main(), 1)
        self.assertEqual(len(self.requests), 2)
        pair = json.loads((output / 'attempt_000/result/pair.json').read_text())
        self.assertTrue(pair['pairing_verified'])
        self.assertEqual(pair['status'], 'completed_with_failures')

    def test_no_request_retries_after_worker_dispatch_error(self):
        with patch.object(self.model, 'generate', side_effect=OSError('Synthetic worker startup failure')):
            code, result = self.solve()
        self.assertEqual(code, 1)
        self.assertEqual(result['generation_requests'], 1)
        self.assertEqual(result['request_outcomes_unknown'], 1)

    def test_total_budget_exhausted_before_generation(self):
        self.config['hls']['total_timeout_seconds'] = .000001
        code, result = self.solve()
        self.assertEqual(code, 1)
        self.assertEqual(result['category'], 'total_timeout')
        self.assertEqual(result['generation_requests'], 0)

    def test_invalid_policy_leaves_failure_receipt(self):
        self.policy['max_repairs'] = -1
        code, result = self.solve()
        self.assertEqual(code, 1)
        self.assertEqual(result['category'], 'policy_error')
        self.assertEqual(self.model.prompts, [])

    def test_multiple_blocks_selected_deterministically_without_retry(self):
        self.model = FakeModel(['```cpp\nint a;\n```\n```cpp\nint b;\n```'])
        code, result = self.solve()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.model.prompts), 1)
        self.assertEqual((self.directory / 'agent/candidate.cpp').read_text(), 'int a;\n')
        details = json.loads((self.directory / 'agent/candidates/000/extraction.json').read_text())
        self.assertEqual(details['selected_block'], 0)
        self.assertEqual(details['tie_break'], 'first_in_response')

    def test_public_top_function_guides_block_selection(self):
        self.public_task()
        self.model = FakeModel(['```cpp\nint helper() { return 0; }\n```\n说明：\n'
                                '```cpp\nint kernel(int a) { return a+1; }\n```'])
        code, result = self.solve()
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual((self.directory / 'agent/candidate.cpp').read_text(), 'int kernel(int a) { return a+1; }\n')
        self.assertEqual(len(self.model.prompts), 1)


if __name__ == '__main__':
    unittest.main()
