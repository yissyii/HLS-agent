"""Linker regressions, including the HLS -> validator -> prompt boundary."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluation.diagnostics import extract_diagnostics
from evaluation.hls import validate_stage
from evaluation.validator import HLSValidator
from evaluation.task_io import load_task
from agent.core.contracts import Candidate
from agent.context.builder import build
from agent.feedback.diagnostics import classify
from tools import test_agent_contract as contract_fixtures


def linker_log(signature, line=43):
    return (
        'Generating csim.exe\n'
        'ld.lld: error: undefined symbol: ' + signature + '\n'
        '>>> referenced by tb.cpp:' + str(line) + ' (../../../../input/tb.cpp:' + str(line) + ')\n'
        '>>>               obj/tb.o:(main)\n'
        'clang++: error: linker command failed with exit code 1 (use -v to see invocation)\n'
        'ERROR: [SIM 211-100] csim_design failed: compilation error(s).\n')


PROB078 = linker_log('TopModule(' + ', '.join(['ap_uint<1>'] * 10 + ['ap_uint<1>&'] * 2) + ')', 42)
PROB110 = linker_log('TopModule(ap_uint<8>, ap_uint<4>&, ap_uint<1>&)')


class LinkerParsing(unittest.TestCase):
    def extract(self, text):
        return extract_diagnostics('csim', {'timed_out': False, 'exit_code': 1}, text)

    def test_real_signatures_references_and_driver(self):
        for text in (PROB078, PROB110):
            with self.subTest(text=text):
                result = self.extract(text)
                self.assertEqual(result['category'], 'compile_error')
                self.assertEqual([e['code'] for e in result['entries']],
                                 ['link_undefined_symbol', 'link_driver_failure'])
                self.assertIn('undefined symbol: TopModule(', result['compiler_text'])
                self.assertIn('referenced by tb.cpp:', result['compiler_text'])
                self.assertIn('obj/tb.o:(main)', result['compiler_text'])
                self.assertNotIn('None:', result['compiler_text'])

    def test_gnu_and_duplicate_symbols(self):
        cases = (
            ("/usr/bin/ld: obj/kernel.o: undefined reference to 'TopModule(int)'",
             'link_undefined_symbol'),
            ("obj/kernel.o: multiple definition of 'TopModule'", 'link_duplicate_symbol'),
            ('ld.lld: error: duplicate symbol: TopModule(int)', 'link_duplicate_symbol'),
            ('collect2: error: ld returned 1 exit status', 'link_driver_failure'),
            ('C:\\tools\\ld.lld.exe: error: undefined symbol: TopModule', 'link_undefined_symbol'),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                result = self.extract(text)
                self.assertEqual(result['entries'][0]['code'], expected)
                self.assertEqual(result['category'], 'compile_error')
                self.assertTrue(result['compiler_text'])

    def test_multiple_blocks_not_consumed_as_source(self):
        result = self.extract('kernel.cpp:1:2: error: bad name\n' + PROB110 + PROB078)
        self.assertIsNone(result['entries'][0]['source'])
        self.assertEqual(len(result['entries']), 5)
        self.assertEqual(result['compiler_text'].count('undefined symbol:'), 2)

    def test_no_oracle_or_unrelated_tail_in_linker_feedback(self):
        result = self.extract(PROB110 + 'Mismatch at cycle 1: expected 987654321, got 0\n'
                              '>>> PRIVATE_ORACLE 987654321\nUNRELATED_SECRET\n')
        self.assertNotIn('987654321', result['compiler_text'])
        self.assertNotIn('UNRELATED_SECRET', result['compiler_text'])
        self.assertNotIn('PRIVATE_ORACLE', result['compiler_text'])

    def test_tool_infrastructure_messages_not_released(self):
        for message, category in (
            ('permission denied', 'environment_or_dependency_error'),
            ('no such file or directory', 'environment_or_dependency_error'),
            ('license checkout failed', 'license_error'),
        ):
            result = self.extract('ld.lld: error: ' + message)
            self.assertEqual(result['category'], category)
            self.assertEqual(result['compiler_text'], '')

    def test_unknown_prefix_and_notes_do_not_create_diagnostics(self):
        for line in ('INFO: ld.lld: error: undefined symbol: SECRET',
                     '>>> referenced by tb.cpp:1', 'random: error: unknown'):
            self.assertEqual(self.extract(line)['compiler_text'], '')

    def test_validate_stage_wires_extraction_not_just_category(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            manifest = dict(cxx_standard='c++14', include_dirs=[], top_function='TopModule',
                            source_file='kernel.cpp', design_files=[], testbench_files=['tb.cpp'],
                            support_files=[])
            settings = dict(part='test', clock_ns=5, csim_timeout_seconds=10)
            def process(command, work, env, log, timeout):
                log.write_text(PROB110)
                return dict(exit_code=1, timed_out=False, log=log.name)
            with patch('evaluation.hls.environment', return_value=('vitis-run', {})), \
                 patch('evaluation.hls.run_process', side_effect=process):
                result = validate_stage('csim', work, manifest, settings, True, 10)
            self.assertEqual(result['status'], 'failed')
            self.assertIn('undefined symbol:', result['compiler_text'])
            self.assertEqual(result['diagnostics'][0]['code'], 'link_undefined_symbol')


class LinkerPromptBoundary(unittest.TestCase):
    # Reuse fixtures, not inherited unrelated timing-sensitive test methods.
    setUpClass = classmethod(contract_fixtures.AgentContract.setUpClass.__func__)
    tearDownClass = classmethod(contract_fixtures.AgentContract.tearDownClass.__func__)
    setUp = contract_fixtures.AgentContract.setUp
    tearDown = contract_fixtures.AgentContract.tearDown
    public_task = contract_fixtures.AgentContract.public_task

    def test_actual_stage_to_prompt_preserves_linker_evidence_and_policy(self):
        for policy in ('category_only', 'compiler_diagnostics', 'functional_diagnostics'):
            with self.subTest(policy=policy):
                self.public_task(feedback=policy)
                task = load_task(self.problem, self.manifest)
                candidate = Candidate(0, 'int kernel(int a){return a;}', self.directory/'link_candidate/candidate.cpp', None)
                def process(command, work, env, log, timeout):
                    log.write_text(PROB110)
                    return dict(exit_code=1, timed_out=False, log=log.name)
                with patch('evaluation.hls.environment', return_value=('vitis-run', {})), \
                     patch('evaluation.hls.run_process', side_effect=process):
                    result = HLSValidator().check(candidate, task, self.config, 'csim', time.monotonic()+10)
                prompt = build(task, self.config, self.policy, candidate, classify(result))
                self.assertEqual('undefined symbol: TopModule(' in prompt.text, policy != 'category_only')
                self.assertEqual(result.checks['compile'], 'failed')


if __name__ == '__main__':
    unittest.main()
