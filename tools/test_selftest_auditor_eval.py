"""Harness checks, not evidence of auditor capability."""
import ast
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import evaluate_selftest_auditor as study


class HarnessTests(unittest.TestCase):
    def test_catalog(self):
        rows, duplicates = study.catalog()
        self.assertEqual(len(rows), 153)
        self.assertEqual(len(duplicates), 9)
        self.assertEqual(len({r['id'] for r in rows}), len(rows))
        self.assertEqual(sum(r['intended'] == 'correct' for r in rows), 36)
        for r in rows:
            self.assertLessEqual(len(list(ast.walk(ast.parse(r['expression'], mode='eval')))), 128)

    def test_translator_rejects_calls(self):
        with self.assertRaises(ValueError):
            study.cpp_expression('f(x)')

    def test_metrics_keep_failure_denominators(self):
        rows = [dict(label='correct'), dict(label='incorrect', audits={'default': {'status': 'crash'}})]
        scores = study.judge_counts(rows)['default']
        self.assertEqual(scores['correct_total'], 1)
        self.assertEqual(scores['correct_not_materialized'], 1)
        self.assertEqual(scores['incorrect_total'], 1)
        self.assertEqual(scores['incorrect_crash'], 1)

    @unittest.skipUnless(shutil.which('g++'), 'Native independent labeling needs g++')
    def test_independent_full_domain_labels(self):
        rows, _ = study.catalog()
        with tempfile.TemporaryDirectory(prefix='auditor-fixture-check-') as tmp:
            source, binary = Path(tmp)/'truth.cpp', Path(tmp)/'truth'
            source.write_text(study.truth_program(rows), encoding='utf-8')
            subprocess.run(['g++', '-std=c++17', '-O2', str(source), '-o', str(binary)],
                           check=True, capture_output=True, timeout=180)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=120)
            labels = {r['id']: r for r in map(json.loads, result.stdout.splitlines())}
        self.assertEqual(len(labels), len(rows))
        for row in rows:
            if row['intended'] == 'correct':
                self.assertEqual(labels[row['id']]['mismatches'], 0, row['id'])
            if row['singleton']:
                self.assertEqual(labels[row['id']]['mismatches'], 1, row['id'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
