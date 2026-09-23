"""Offline contracts, adversarial cases, loopback model transport and optional C++ integration."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.selftest.generate import generate_suite, load_suite, make_prompt
from agent.selftest.oracle import Expression, expand
from agent.selftest.runner import observations, run_suite
from agent.selftest.spec import PublicTask, SelftestError, parse_interface, parse_json
from serve.agent_model import ModelClient
from serve.inference import load_config
from tools.prepare_selftest_examples import EXAMPLES, response_for, prepare
from tools.evaluate_selftest import evaluate


class SelftestContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='zcomp-selftest-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.item = EXAMPLES[3]
        self.task = PublicTask(self.item['problem'], '#pragma once\n#include <stdint.h>\nuint8_t xor_mask(uint8_t x);\n')
        self.bundle = response_for(self.item)

    def generate(self, name='suite', **options):
        return generate_suite(self.task, self.root / name, response=self.bundle, **options)

    def test_recorded_pipeline_and_freeze(self):
        result = self.generate()
        self.assertEqual(result['status'], 'generated_unreviewed')
        self.assertEqual(result['generation_requests'], 0)
        self.assertTrue(result['review_required'])
        manifest, _, _, vectors = load_suite(self.root / 'suite')
        self.assertEqual(len(vectors), 256)
        self.assertTrue(manifest['coverage']['exhaustive_over_declared_domain'])
        self.assertEqual(vectors[0]['expected'], 165)

    def test_output_is_exclusive(self):
        self.generate()
        with self.assertRaises(FileExistsError):
            self.generate()

    def test_prompt_excludes_candidate_and_private_files(self):
        (self.root / 'solution.cpp').write_text('PRIVATE_CANDIDATE_SENTINEL')
        prompt = make_prompt('contract', self.task)
        self.assertNotIn('PRIVATE_CANDIDATE_SENTINEL', prompt.text)
        self.assertEqual(set(json.loads(prompt.text)), {'public_problem', 'public_interface', 'max_cases'})
        self.assertFalse(prompt.context['candidate_visible'])

    def test_unsupported_interfaces(self):
        for header in ['void f(int* p);', 'int f(int x);', 'uint8_t f(uint8_t x[4]);',
                       'uint8_t f(uint8_t x); uint8_t g(uint8_t x);',
                       '#include "/secret.h"\nuint8_t f(uint8_t x);',
                       '#define f(x) 0\nuint8_t f(uint8_t x);']:
            with self.subTest(header=header), self.assertRaises(SelftestError):
                parse_interface(header)

    def test_ap_int_supported(self):
        signature = parse_interface('#include <ap_int.h>\nap_int<8> f(ap_uint<4> a);')
        self.assertEqual(signature['output_type'], 'ap_int<8>')
        with self.assertRaises(SelftestError):
            parse_interface('ap_int<80> f(ap_uint<4> a);')

    def test_exact_citations(self):
        self.bundle['contract']['rules'][0]['evidence'][0]['quote'] = 'invented requirement'
        self.assertEqual(self.generate()['status'], 'failed')

    def test_signature_change_rejected(self):
        self.bundle['contract']['inputs'][0]['type'] = 'int8_t'
        self.assertEqual(self.generate()['status'], 'failed')

    def test_domain_cannot_exceed_type(self):
        self.bundle['contract']['inputs'][0]['domain'] = [0, 256]
        self.assertEqual(self.generate()['status'], 'failed')

    def test_uncertainty_blocks_without_plan(self):
        self.bundle['contract']['uncertainties'] = ['Reset semantics are unspecified.']
        self.bundle['test_plan'] = None
        result = self.generate()
        self.assertEqual(result['status'], 'blocked_specification')
        self.assertFalse((self.root / 'suite/testbench.cpp').exists())
        self.assertFalse((self.root / 'suite/generation_plan').exists())

    def test_json_rejects_duplicates_nan_and_prose(self):
        for raw in ['{"x":1,"x":2}', '{"x":NaN}', '{} trailing', '```cpp\n{}\n```']:
            with self.assertRaises(SelftestError):
                parse_json(raw)
        self.assertEqual(parse_json('```json\n{"x":1}\n```'), {'x': 1})

    def test_expression_has_no_execution_or_unsafe_syntax(self):
        for expression in ['__import__("os")', 'x.__class__', 'x[0]', '[x for x in x]', '2**63', 'x/2', 'x//2', '1.0', 'y+1']:
            with self.subTest(expression=expression), self.assertRaises(SelftestError):
                Expression(expression, ['x'])

    def test_expression_numeric_limits(self):
        for expression in ['x << 64', 'x << -1', 'x * 18446744073709551615']:
            with self.assertRaises(SelftestError):
                Expression(expression, ['x'])({'x': 2})
        self.assertEqual(Expression('x if x<0 else -x', ['x'])({'x': -8}), -8)

    def test_no_silent_output_wrapping(self):
        self.bundle['test_plan']['oracle']['expression'] = 'x+1'
        self.assertEqual(self.generate()['status'], 'failed')

    def test_no_silent_budget_truncation(self):
        self.assertEqual(self.generate(max_cases=10)['status'], 'failed')

    def test_unknown_rule_rejected(self):
        self.bundle['test_plan']['oracle']['rule_ids'] = ['MISSING']
        self.assertEqual(self.generate()['status'], 'failed')

    def test_random_reproducibility(self):
        self.bundle['test_plan']['sampling'] = dict(mode='boundary_random', seed=4, random_cases=100)
        first = expand(self.bundle['test_plan'], self.bundle['contract'])[0]
        self.assertEqual(first, expand(self.bundle['test_plan'], self.bundle['contract'])[0])
        self.bundle['test_plan']['sampling']['seed'] = 5
        self.assertNotEqual(first, expand(self.bundle['test_plan'], self.bundle['contract'])[0])

    def test_tampering_rejected(self):
        self.generate()
        path = self.root / 'suite/testbench.cpp'
        path.write_text(path.read_text() + '\n// changed')
        with self.assertRaises(SelftestError):
            load_suite(self.root / 'suite')

    def test_missing_execution_authority(self):
        self.generate()
        result = run_suite(self.root / 'suite', self.root / 'missing.cpp', self.root / 'run')
        self.assertEqual(result['status'], 'inconclusive')
        self.assertIn('--allow-execution', result['message'])

    def test_strict_observation_protocol(self):
        vectors = [dict(id=0, inputs=[0], expected=1, rule_ids=['R1'], origin='explicit')]
        ok = 'SELFTEST_CASE {"case_id":0,"actual":1}\nSELFTEST_END {"count":1,"failures":0}\n'
        self.assertEqual(observations(ok, vectors, True)['status'], 'selftest_passed')
        bad = ok.replace('"actual":1', '"actual":0').replace('"failures":0', '"failures":1')
        self.assertEqual(observations(bad, vectors, False)['first_failure']['actual'], 0)
        for log, passed in [('', True), (ok, False), (bad, True), (ok+ok, True),
                            (ok.replace('"count":1', '"count":0'), True),
                            (ok.replace('"case_id":0', '"case_id":true'), True)]:
            with self.assertRaises(SelftestError):
                observations(log, vectors, passed)

    def test_all_synthetic_fixtures_materialize(self):
        root = prepare(self.root / 'examples')
        manifest = json.loads((root / 'fixtures.json').read_text())
        self.assertEqual(len(manifest['tasks']), 7)
        for task in manifest['tasks']:
            load_suite(root / 'suites' / task['id'])

    def test_invalid_control_excludes_mutant_kills(self):
        root = prepare(self.root / 'examples')
        def fake_run(suite, source, output, **options):
            return dict(status='selftest_failed', mismatch_count=1, elapsed_seconds=0,
                        suite_id=load_suite(suite)[0]['suite_id'])
        with patch('tools.evaluate_selftest.run_suite', side_effect=fake_run):
            result = evaluate(root, self.root / 'eval')
        self.assertEqual(result['false_positives'], 5)
        self.assertEqual(result['eligible_mutants'], 0)
        self.assertEqual(result['functional_kills'], 0)
        self.assertIsNone(result['mutation_detection_rate'])

    def test_two_stage_real_transport_loopback(self):
        requests = []
        bundle = self.bundle
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                content = bundle['contract' if len(requests) == 1 else 'test_plan']
                body = json.dumps({'choices': [{'message': {'content': json.dumps(content)}, 'finish_reason': 'stop'}]}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            runtime = load_config(ROOT / 'agent/selftest/config/runtime.wsl.example.json', frozen=True, allow_external=True)
            runtime['model']['base_url'] = f'http://127.0.0.1:{server.server_port}/v1'
            result = generate_suite(self.task, self.root / 'live', model=ModelClient(), runtime=runtime)
            self.assertEqual(result['status'], 'generated_unreviewed', result)
            self.assertEqual(result['generation_requests'], 2)
            self.assertEqual(len(requests), 2)
            self.assertNotIn('contract', json.loads(requests[0]['messages'][1]['content']))
            self.assertIn('contract', json.loads(requests[1]['messages'][1]['content']))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    @unittest.skipUnless(shutil.which('g++'), 'Native compiler not on PATH; run in WSL for integration')
    def test_native_correct_mutant_and_build_failure(self):
        self.generate()
        for name, body, expected in [('correct', 'return x^165;', 'selftest_passed'),
                                     ('wrong', 'return x|165;', 'selftest_failed'),
                                     ('broken', 'return undeclared;', 'candidate_build_error')]:
            source = self.root / (name + '.cpp')
            source.write_text('#include "interface.h"\nuint8_t xor_mask(uint8_t x){' + body + '}')
            result = run_suite(self.root / 'suite', source, self.root / name, allow_execution=True)
            self.assertEqual(result['status'], expected, result)


if __name__ == '__main__':
    unittest.main()
