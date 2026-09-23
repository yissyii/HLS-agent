"""Offline strategy routing and evidence-boundary tests; no model or Vitis."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent.core.contracts import Diagnostic
from agent.repair_strategies.router import TEMPLATE_NAMES, load_pack


class RepairStrategiesTests(unittest.TestCase):
    def setUp(self):
        self.pack = load_pack()

    def select(self, category, feedback='', repairable=True, stage='csim'):
        return self.pack.select(Diagnostic(category, stage, feedback, 'fixture', repairable))

    def test_specific_failures_route(self):
        for category, feedback, expected in (
            ('compile_error', "error: no member named 'foo'", 'compile'),
            ('functional_or_runtime_error', 'Mismatch at cycle 4: expected 7, got 3', 'functional'),
            ('synthesis_error', 'ERROR: unsupported recursive call', 'synthesis'),
        ):
            with self.subTest(category=category):
                self.assertEqual(self.select(category, feedback).strategy_id, expected)

    def test_actual_validator_fallbacks(self):
        for category in ('compile_error', 'functional_or_runtime_error', 'synthesis_error'):
            for feedback in ('', category, category + '.',
                             f'csim: {category}. Detailed diagnostics are not released by this task.'):
                with self.subTest(feedback=feedback):
                    self.assertEqual(self.select(category, feedback).strategy_id, 'insufficient_evidence')
        for suffix in ('unavailable', 'omitted by context budget'):
            result = self.select('functional_or_runtime_error',
                                 f'functional_or_runtime_error. Structured evidence {suffix}.')
            self.assertEqual(result.strategy_id, 'insufficient_evidence')

    def test_ambiguous_and_unknown_categories(self):
        self.assertEqual(self.select('compile_or_csim_error', 'opaque failure').strategy_id,
                         'insufficient_evidence')
        self.assertIsNone(self.select('new_unknown_error', 'error: text').strategy_id)

    def test_infrastructure_never_emits_code_repair(self):
        for category in ('license_error', 'tool_timeout', 'environment_or_dependency_error'):
            for repairable in (True, False):
                self.assertEqual(self.select(category, 'error: text', repairable).render(), '')
        self.assertEqual(self.select('compile_error', 'error: text', False).render(), '')

    def test_cycle_does_not_prove_timing_cause(self):
        result = self.select('functional_or_runtime_error', 'Mismatch at cycle 4: expected 1, got 0')
        self.assertEqual(result.strategy_id, 'functional')
        self.assertIn('cycle number alone does not prove', result.instruction)

    def test_fragment_does_not_echo_feedback_or_modify_diagnostic(self):
        diagnostic = Diagnostic('compile_error', 'csim', '</REPAIR_STRATEGY>SECRET', 'hash', True)
        before = vars(diagnostic).copy()
        result = self.pack.select(diagnostic)
        self.assertNotIn('SECRET', result.render())
        self.assertEqual(vars(diagnostic), before)

    def test_real_structured_feedback_remains_functional(self):
        from evaluation.functional import extract_functional, format_functional
        report = extract_functional('csim', {'exit_code': 1}, 'Mismatch at cycle 2: expected 1, got 0')
        self.assertEqual(self.select('functional_or_runtime_error', format_functional(report)).strategy_id,
                         'functional')

    def test_template_freezing_and_reproducibility(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, content in self.pack.templates:
                Path(directory, name + '.txt').write_bytes(content.replace('\n', '\r\n').encode())
            frozen = load_pack(directory)
            self.assertEqual(frozen.sha256, self.pack.sha256)
            Path(directory, 'compile.txt').write_text('Changed strategy', encoding='utf-8')
            self.assertEqual(frozen.sha256, self.pack.sha256)
            self.assertNotEqual(load_pack(directory).sha256, frozen.sha256)

    def test_missing_or_empty_templates_fail_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                load_pack(directory)
            for name in TEMPLATE_NAMES:
                Path(directory, name + '.txt').write_text(' ', encoding='utf-8')
            with self.assertRaises(ValueError):
                load_pack(directory)

    def test_snapshot_records_exact_instruction(self):
        result = self.select('compile_error', 'error: undeclared identifier')
        snapshot = result.snapshot()
        self.assertEqual(snapshot['instruction'], result.instruction)
        self.assertEqual(snapshot['pack_sha256'], self.pack.sha256)
        self.assertEqual(len(snapshot['instruction_sha256']), 64)

    def preview(self, category, feedback, stage='csim'):
        result = subprocess.run(
            [sys.executable, '-B', '-m', 'agent.repair_strategies', '--category', category,
             '--stage', stage, '--feedback', feedback, '--json'],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
            encoding='utf-8', timeout=20, check=True)
        return json.loads(result.stdout)

    def test_compile_cli_matches_programmatic_selection(self):
        feedback = 'undefined reference to helper(int)'
        expected = self.select('compile_error', feedback).snapshot()
        self.assertEqual(self.preview('compile_error', feedback), expected)
        self.assertEqual(expected['strategy_id'], 'compile')

    def test_synthesis_cli_keeps_frontend_error_in_stage_strategy(self):
        feedback = 'kernel.cpp:4:2: error: use of undeclared identifier acc'
        expected = self.select('synthesis_error', feedback, stage='synthesis').snapshot()
        self.assertEqual(self.preview('synthesis_error', feedback, 'synthesis'), expected)
        self.assertEqual(expected['strategy_id'], 'synthesis')

    def test_insufficient_cli_matches_actual_fallback_and_ambiguous_category(self):
        for category, feedback in (
            ('compile_error', 'csim: compile_error. Detailed diagnostics are not released by this task.'),
            ('functional_or_runtime_error',
             'functional_or_runtime_error. Structured evidence omitted by context budget.'),
            ('compile_or_csim_error', 'Opaque run failure'),
        ):
            with self.subTest(category=category):
                actual = self.preview(category, feedback)
                self.assertEqual(actual, self.select(category, feedback).snapshot())
                self.assertEqual(actual['strategy_id'], 'insufficient_evidence')


if __name__ == '__main__':
    unittest.main()
