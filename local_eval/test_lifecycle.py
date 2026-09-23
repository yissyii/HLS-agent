"""Development lifecycle regressions using synthetic tasks and loopback HTTP."""
import argparse
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from agent.interface import entry
from evaluation import lifecycle, single_task, batch
from local_eval import guard
from local_eval.retry import classify, load_settings, run_session
from serve import baseline_entry, baseline, paired_entry
from serve.inference import ROOT, write_json
from tools.test_agent_contract import FakeValidator
from tools import run_bench4hls_compare as compare


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lifecycle_test_', dir=ROOT / 'output')
        self.root = Path(self.temp.name)
        self.requests, self.responses = [], []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                owner.requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                response = owner.responses.pop(0) if owner.responses else 200
                if response == 'disconnect':
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                data = json.dumps({'choices': [{'message': {'content': 'int kernel(int a) { return a+1; }'},
                                                'finish_reason': 'length' if response == 'length' else 'stop'}]}).encode()
                self.send_response(response if isinstance(response, int) else 200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        runtime = json.loads((ROOT / 'serve/runtime.json').read_text(encoding='utf-8'))
        runtime['model'].update(base_url='http://127.0.0.1:%d/v1' % self.server.server_port,
                                tls_sha256='', max_tokens=100, context_tokens=8192, timeout_seconds=10)
        runtime['hls']['total_timeout_seconds'] = 30
        self.config = self.root / 'runtime.json'
        write_json(self.config, runtime)
        self.problem = self.root / 'problem.txt'
        self.problem.write_text('Implement int kernel(int a): return a+1.', encoding='utf-8')
        self.settings = load_settings()
        self.settings.update(initial_delay_seconds=0, max_delay_seconds=0)
        self.settings_patch = patch.object(guard, 'load_settings', return_value=self.settings)
        self.settings_patch.start()

    def tearDown(self):
        self.settings_patch.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def invoke(self, main, args):
        with patch.object(sys, 'argv', ['test', *map(str, args)]), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main()

    def session(self, output):
        return json.loads((output / 'session.json').read_text(encoding='utf-8'))

    def task(self, name):
        directory = self.root / 'dataset' / name
        directory.mkdir(parents=True)
        (directory / 'problem.txt').write_bytes(self.problem.read_bytes())
        (directory / 'tb.cpp').write_text('int main(){return 0;}', encoding='utf-8')
        write_json(directory / 'task.json', dict(id=name, top_function='kernel', problem_file='problem.txt',
                                                source_file='kernel.cpp', testbench_files=['tb.cpp']))
        return directory / 'task.json'

    def test_agent_retries_request_without_restarting(self):
        self.responses = [503, 200]
        output = self.root / 'agent'
        self.assertEqual(self.invoke(entry.main, [self.problem, output, '--config', self.config]), 0)
        summary = self.session(output)
        self.assertEqual(summary['valid_attempt'], 'attempt_000')
        self.assertEqual(summary['api_requests_recorded_all_attempts'], 2)
        self.assertEqual(self.requests[0], self.requests[1])
        self.assertFalse((output / 'attempt_000/discarded.json').exists())
        self.assertTrue((output / 'attempt_000/result/candidate.cpp').is_file())
        self.assertEqual(len(list(output.glob('attempt_*'))), 1)
        self.assertNotIn(guard.SCOPE_ENV, os.environ)

    def test_baseline_entry_and_disconnect_recovery(self):
        self.responses = ['disconnect', 200]
        output = self.root / 'baseline'
        self.assertEqual(self.invoke(baseline_entry.main, [self.problem, output, '--config', self.config]), 0)
        self.assertEqual(self.session(output)['valid_attempt'], 'attempt_000')
        self.assertEqual(len(self.requests), 2)

    def test_legacy_file_entry_exports_only_accepted_response(self):
        self.responses = [503, 200]
        output = self.root / 'answer.cpp'
        self.assertEqual(self.invoke(baseline.main, [self.problem, output, '--config', self.config]), 0)
        self.assertTrue(output.is_file())
        self.assertEqual(self.session(Path(str(output) + '.evaluation'))['valid_attempt'], 'attempt_000')

    def test_paired_preserves_successful_baseline(self):
        self.responses = [200, 503, 200, 200]
        output = self.root / 'pair'
        self.assertEqual(self.invoke(paired_entry.main, [self.problem, output, '--config', self.config]), 0)
        self.assertEqual(len(self.requests), 3)
        summary = self.session(output)
        self.assertEqual(summary['valid_attempt'], 'attempt_000')
        first = output / 'attempt_000/result/baseline/result.json'
        self.assertEqual(json.loads(first.read_text())['generation_requests'], 1)
        self.assertEqual(len(list(output.rglob('session.json'))), 1)

    def test_baseline_network_fault_recovers_then_runs_agent(self):
        self.responses = [503, 200, 200]
        output = self.root / 'pair'
        self.assertEqual(self.invoke(paired_entry.main, [self.problem, output, '--config', self.config]), 0)
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.session(output)['valid_attempt'], 'attempt_000')

    def test_legacy_single_task_enters_scope_and_recovers(self):
        manifest = self.task('synthetic')
        self.responses = [503, 200]
        captured = []
        original = guard.run_session
        def capture(output, *args, **kwargs):
            captured.append(output)
            return original(output, *args, **kwargs)
        with patch.object(guard, 'run_session', side_effect=capture), patch('agent.interface.entry.HLSValidator', return_value=FakeValidator()):
            self.assertEqual(self.invoke(single_task.main, [manifest, '--config', self.config]), 0)
        self.assertEqual(len(captured), 1)
        self.assertEqual(self.session(captured[0])['valid_attempt'], 'attempt_000')

    def test_batch_preserves_completed_sibling(self):
        for name in ('one', 'two'):
            self.task(name)
        self.responses = [200, 503, 200, 200]
        captured = []
        original = guard.run_session
        def capture(output, *args, **kwargs):
            captured.append(output)
            return original(output, *args, **kwargs)
        def child(command, **kwargs):
            buffer = io.StringIO()
            with patch.object(sys, 'argv', ['child', *command[3:]]), contextlib.redirect_stdout(buffer):
                code = single_task.main()
            return subprocess.CompletedProcess(command, code, stdout=buffer.getvalue(), stderr='')
        with patch.object(guard, 'run_session', side_effect=capture), \
             patch.object(batch.subprocess, 'run', side_effect=child), \
             patch('agent.interface.entry.HLSValidator', return_value=FakeValidator()):
            self.assertEqual(self.invoke(batch.main, [self.root / 'dataset', '--config', self.config]), 0)
        self.assertEqual(len(captured), 1)
        summary = self.session(captured[0])
        self.assertEqual(summary['valid_attempt'], 'attempt_000')
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(len(list(captured[0].rglob('session.json'))), 1)

    def test_compare_retries_only_failed_request(self):
        self.task('custom_name')
        self.responses = [200, 503, 200, 200]
        captured = []
        original = guard.run_session
        def capture(output, *args, **kwargs):
            captured.append(output)
            return original(output, *args, **kwargs)
        def child(command, **kwargs):
            buffer = io.StringIO()
            if command[2] == 'serve/baseline_entry.py':
                target, args = baseline_entry.main, command[3:]
            else:
                target = single_task.main if command[3] == 'evaluation.single_task' else entry.main
                args = command[4:]
            with patch.object(sys, 'argv', ['child', *args]), contextlib.redirect_stdout(buffer):
                code = target()
            return subprocess.CompletedProcess(command, code, stdout=buffer.getvalue(), stderr='')
        with patch.object(guard, 'run_session', side_effect=capture), patch.object(compare, '_run', side_effect=child), \
             patch.object(entry.signal, 'signal'), \
             patch('agent.interface.entry.HLSValidator', return_value=FakeValidator()):
            self.assertEqual(self.invoke(compare.main, ['--dataset', self.root / 'dataset', '--config', self.config, '--workers', '1']), 0)
        self.assertEqual(len(captured), 1)
        self.assertEqual(self.session(captured[0])['valid_attempt'], 'attempt_000')
        self.assertEqual(len(self.requests), 3)

        prior = next(captured[0].rglob('summary.json'))
        with patch.object(guard, 'run_session', side_effect=capture), patch.object(compare, '_run') as child_call:
            self.assertEqual(self.invoke(compare.main, [
                '--dataset', self.root / 'dataset', '--config', self.config, '--workers', '1',
                '--resume-summary', prior]), 0)
            child_call.assert_not_called()
        resumed = json.loads(next(captured[1].rglob('summary.json')).read_text())
        self.assertEqual(resumed['retained_count'], 2)

    def test_persistent_network_failure_exhausts_limit(self):
        self.responses = [503, 503, 503]
        output = self.root / 'failed'
        self.assertEqual(self.invoke(baseline_entry.main, [self.problem, output, '--config', self.config]), 2)
        summary = self.session(output)
        self.assertEqual(summary['valid_attempt'], 'attempt_000')
        self.assertEqual(summary['status'], 'completed_with_network_failures')
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(len(summary['attempts']), 1)
        self.assertTrue(all(not a['valid_for_metrics'] for a in summary['attempts']))

    def test_non_network_failures_are_accepted_without_retry(self):
        for response in (400, 'length'):
            self.responses = [response]
            output = self.root / str(response)
            self.assertEqual(self.invoke(baseline_entry.main, [self.problem, output, '--config', self.config]), 1)
            self.assertEqual(self.session(output)['valid_attempt'], 'attempt_000')
            self.assertEqual(len(self.session(output)['attempts']), 1)

    def test_external_service_errors_retry_without_extra_candidates(self):
        for status in (401, 404, 429, 500, 502, 503, 504):
            self.responses = [status, 200]
            output = self.root / ('external_' + str(status))
            before = len(self.requests)
            self.assertEqual(self.invoke(baseline_entry.main, [self.problem, output, '--config', self.config]), 0)
            self.assertEqual(len(self.requests) - before, 2)
            receipt = json.loads((output / 'attempt_000/result/result.json').read_text())
            self.assertEqual(receipt['retries'], 1)
            self.assertEqual(receipt['generation_requests'], 2)
            self.assertEqual(self.requests[-1], self.requests[-2])

    def test_infrastructure_is_not_an_ability_failure(self):
        from evaluation.metrics import summarize
        rows = [
            dict(task='one', method='baseline', overall=True),
            dict(task='two', method='baseline', reason='api_auth_error'),
            dict(task='three', method='baseline', reason='response_format_error'),
            dict(task='one', method='agent', stop_reason='compile_error'),
        ]
        report = summarize(rows)
        self.assertEqual(report['baseline']['scored'], 2)
        self.assertEqual(report['baseline']['pending'], 1)
        self.assertEqual(report['baseline']['pass_rate'], .5)
        self.assertEqual(report['paired'], dict(tasks=1, baseline=1, agent=0))

    def test_only_transport_evidence_classifies_as_retryable(self):
        for status in (408, 429, 500, 502, 503, 504):
            self.assertIsNotNone(classify(dict(category='api_http_error', http_status=status), self.settings))
        for category in ('compile_error', 'synthesis_error', 'functional_or_runtime_error',
                         'tool_timeout', 'total_timeout', 'generation_timeout', 'generation_incomplete'):
            self.assertIsNone(classify(dict(category=category, message='503'), self.settings))

    def test_removing_module_restores_single_attempt(self):
        args = argparse.Namespace(config=str(self.config))
        with patch.object(lifecycle, 'import_module', side_effect=ModuleNotFoundError(name='local_eval')):
            self.assertEqual(lifecycle.evaluate(args, lambda current: 7), 7)
            self.assertEqual(lifecycle.output_path(self.root), self.root)
        with patch.object(lifecycle, 'import_module', side_effect=ModuleNotFoundError(name='broken_dependency')):
            with self.assertRaises(ModuleNotFoundError):
                lifecycle.evaluate(args, lambda current: 0)

    def test_interruption_does_not_restart(self):
        output = self.root / 'interrupted'
        args = argparse.Namespace(config=str(self.config))
        def stop(current):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt), contextlib.redirect_stdout(io.StringIO()):
            lifecycle.evaluate(args, stop, output=output)
        self.assertEqual(self.session(output)['status'], 'interrupted')
        self.assertNotIn(guard.SCOPE_ENV, os.environ)

    def test_changed_inputs_prevent_retry(self):
        output = self.root / 'changed'
        args = argparse.Namespace(config=str(self.config), problem=str(self.problem))
        def change(current):
            self.problem.write_text('Changed mid-evaluation', encoding='utf-8')
            return 0
        with self.assertRaisesRegex(Exception, 'inputs changed'), contextlib.redirect_stdout(io.StringIO()):
            lifecycle.evaluate(args, change, output=output)
        self.assertIsNone(self.session(output)['valid_attempt'])


if __name__ == '__main__':
    unittest.main()
