"""Fixed draft, scheduling, runtime and accounting contracts without real services."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from tools import run_rag_compare as compare
from rag.common import file_sha256
from serve.inference import ROOT, write_json


class CompareContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'output')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def valid_draft(self, directory):
        p = directory/'candidates/000/candidate.cpp'
        p.parent.mkdir(parents=True)
        p.write_text('int kernel(int x){return x;}\n')
        write_json(directory/'result.json', {
            'stages': {'generation': {'status': 'passed', 'finish_reason': 'stop'}},
            'candidates': [{'candidate_id': 0, 'source_sha256': file_sha256(p)}]})
        return p

    def test_raw_or_truncated_response_is_not_a_valid_draft(self):
        source = self.valid_draft(self.root)
        self.assertEqual(compare._valid_draft(self.root), source)
        receipt = json.loads((self.root/'result.json').read_text())
        receipt['stages']['generation']['finish_reason'] = 'length'
        write_json(self.root/'result.json', receipt)
        self.assertIsNone(compare._valid_draft(self.root))
        (source.parent/'response.txt').write_text('some truncated text')
        source.unlink()
        self.assertIsNone(compare._valid_draft(self.root))

    def test_draft_hash_mismatch_is_not_reused(self):
        source = self.valid_draft(self.root)
        source.write_text('different')
        self.assertIsNone(compare._valid_draft(self.root))

    def test_comparison_cannot_silently_generate_a_new_draft(self):
        with patch.object(compare, '_run', side_effect=AssertionError('must not dispatch')):
            with self.assertRaisesRegex(ValueError, 'valid frozen'):
                compare._entry(self.root, self.root/'out', 'config', 'hybrid', 'policy', 'custom-runtime')

    def test_custom_runtime_is_forwarded_and_receipt_checked(self):
        initial = self.root/'initial.cpp'; initial.write_text('int a;')
        output = self.root/'out'; output.mkdir()
        write_json(output/'result.json', {'candidates': [{'candidate_id': 0, 'source_sha256': file_sha256(initial)}],
                                         'checks': {'run': 'passed', 'synthesize': 'passed'}})
        result = types.SimpleNamespace(stdout='', stderr='', returncode=0)
        with patch.object(compare, '_run', return_value=result) as run:
            row = compare._entry(self.root, output, 'config', 'hybrid', 'policy', 'custom-runtime', initial)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('--rag-runtime')+1], 'custom-runtime')
        self.assertTrue(row['initial_source_verified'])

    def test_all_conditions_reuse_same_valid_source_invalid_draft_excluded(self):
        tasks = [self.root/'task1', self.root/'task2']
        batch = self.root/'batch'
        config = ROOT/'serve/runtime.json'
        args = argparse.Namespace(config=str(config), policy=str(ROOT/'agent/config/policy.rag-hybrid.json'),
                                  rag_runtime='explicit-runtime', workers=1, drafts_from=None)
        calls = []
        def entry(task, output, configuration, condition, policy, runtime, source=None, validation=None, **kwargs):
            calls.append((task.name, condition, source, policy, runtime))
            if condition == 'draft' and task.name == 'task1':
                self.valid_draft(output)
            return dict(task=task.name, method=condition, stage='validated',
                        checks={'compile': 'passed', 'run': 'failed'},
                        overall=condition == 'hybrid', api_requests=1, elapsed_seconds=1,
                        stop_reason='repair_budget_exhausted', exit_code=0)
        with patch.object(compare, '_discover', return_value=tasks), \
             patch.object(compare, 'output_path', return_value=batch), \
             patch.object(compare, 'Retrieval'), \
             patch.object(compare, 'load_runtime', return_value=(None, {'registry':'chosen-release','python':None,'model':'chosen-model'})), \
             patch.object(compare, '_entry', side_effect=entry), contextlib.redirect_stdout(io.StringIO()):
            code = compare._evaluate(args)
        self.assertEqual(code, 0)
        repairs = [c for c in calls if c[1] != 'draft']
        self.assertEqual([c[1] for c in repairs], ['off', 'bm25', 'hybrid'])
        self.assertEqual(len({c[2] for c in repairs}), 1)
        for _, _, _, path, rt in repairs:
            policy = json.loads(path.read_text())
            self.assertEqual(policy['max_repairs'], 2)
            self.assertEqual(json.loads(rt.read_text())['model'], 'chosen-model')
        summary = json.loads((batch/'summary.json').read_text())
        excluded = [r for r in summary['results'] if r['task'] == 'task2']
        self.assertEqual(len(excluded), 3)
        self.assertTrue(all(r['stop_reason'] == 'no_valid_initial_source' for r in excluded))
        self.assertEqual(summary['comparison']['hybrid']['recovery_denominator'], 1)
        self.assertEqual(summary['paired_vs_off']['hybrid']['wins'], ['task1'])

    def test_compare_freezes_hybrid_even_if_base_policy_disables_rag(self):
        from local_eval.guard import input_files
        args = argparse.Namespace(policy=str(ROOT/'agent/config/policy.json'), rag_compare=True,
                                  rag_runtime='explicit-runtime')
        with patch('local_eval.guard.input_artifacts', return_value=[]) as artifacts:
            input_files(args)
        policy, runtime = artifacts.call_args.args
        self.assertTrue(policy['rag_enabled'])
        self.assertEqual(policy['rag_mode'], 'hybrid')
        self.assertEqual(runtime, 'explicit-runtime')


if __name__ == '__main__': unittest.main()
