import hashlib, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from tools.bench4hls_behavior_stress_cases import build_cases
from tools import replay_bench4hls_reference_gate as adapter

class AdapterTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_finite_table_compiles_supported_sum_logic_and_counter_cases(self):
        for case_id in ('Sum01', 'Logic01', 'Count01'):
            case = next(x for x in build_cases(7) if x['case_id'] == case_id)
            table = adapter.finite_table(case, 'a' * 64)
            oracle = adapter.compile_oracle(table, case['public_problem'].encode(), case['top'])
            expected = len(table['states'])
            for p in oracle.signature['inputs']:
                expected *= 1 << p['width']
            with self.subTest(case_id=case_id):
                self.assertEqual(len(table['transitions']), expected)
                self.assertTrue(all(set(x['outputs']) == {oracle.signature['outputs'][0]['name']}
                                    for x in table['transitions']))

    def test_qualification_requires_recomputed_matching_controls_and_suite(self):
        q = {'status': 'qualified'}
        with patch.object(adapter, 'read', side_effect=[q, {'suite_manifest_sha256': 'x'}]), patch.object(adapter, 'assess_controls', return_value=q):
            self.assertFalse(adapter.qualification_ok('q.json', 'y'))

    def _mini_inputs(self, *, duplicate_attempt=False):
        cases = [next(x for x in build_cases(7) if x['case_id'] == case_id)
                 for case_id in ('Sum01', 'Negative01')]
        suite = self.root / 'suite'; reference = suite / 'reference'; reference.mkdir(parents=True)
        (suite / 'manifest.json').write_text(json.dumps({'mini': True}), encoding='utf8')
        (reference / 'cases.json').write_text(json.dumps(cases), encoding='utf8')
        suite_hash = adapter.sha((suite / 'manifest.json').read_bytes())
        frozen = {'cases': [dict(case_id=x['case_id'], expected_scope=x['expected_scope']) for x in cases],
                  'source_hashes': {}}
        run = self.root / 'run'; run.mkdir()
        manifest = {'representation': 'behaviors', 'repeats': 1, 'suite_manifest_sha256': suite_hash}
        (run / 'manifest.json').write_text(json.dumps(manifest), encoding='utf8')
        attempts = [dict(task_id=x['case_id'], run_index=1) for x in cases]
        if duplicate_attempt:
            attempts.append(dict(attempts[0]))
        summary = {'status': 'completed', 'attempts': attempts, 'total': 2, 'finished': 2}
        (run / 'summary.json').write_text(json.dumps(summary), encoding='utf8')
        audit = {'suite_manifest_sha256': suite_hash,
                 'run_manifest_sha256': adapter.sha((run / 'manifest.json').read_bytes()),
                 'report_integrity_errors': [],
                 'rows': [dict(case_id=x['case_id'], run_index=1, generation_status='generated_unreviewed') for x in cases]}
        (run / 'audit_v1.json').write_text(json.dumps(audit), encoding='utf8')
        for case in cases:
            (run / case['case_id'] / 'run_001' / 'generation').mkdir(parents=True)
        qualification = self.root / 'qualification.json'; qualification.write_text(json.dumps({'status': 'qualified'}), encoding='utf8')
        return suite, run, qualification, frozen, cases

    def test_replay_mini_fixture_calls_both_scopes_and_snapshots_sources(self):
        suite, run, qualification, frozen, cases = self._mini_inputs()
        output = self.root / 'out'

        def fake_review(problem, top, generation, output, **options):
            output.mkdir(parents=True)
            status = 'reference_checked' if options.get('oracle_path') else 'abstained'
            report = {'status': status, 'export_for_review_allowed': status == 'reference_checked'}
            (output / 'report.json').write_text(json.dumps(report), encoding='utf8')
            return report

        with patch.object(adapter, 'verify_suite', return_value=frozen), \
             patch.object(adapter, 'qualification_ok', return_value=True), \
             patch.object(adapter, 'review_testbench', side_effect=fake_review) as review:
            summary = adapter.replay(suite, [run], output, qualification)
        self.assertEqual(review.call_count, 2)
        self.assertEqual(summary['status'], 'completed')
        self.assertEqual((summary['total_attempts'], summary['finished']), (2, 2))
        self.assertEqual([row['status'] for row in summary['rows']], ['reference_checked', 'abstained'])
        self.assertEqual(summary['model_requests_this_replay'], 0)
        manifest = adapter.read(output / 'manifest.json')
        self.assertEqual(manifest['model_calls'], 0)
        self.assertTrue((output / 'sources/tools/replay_bench4hls_reference_gate.py').is_file())
        self.assertEqual(adapter.sha((output / 'manifest.json').read_bytes()), summary['manifest_sha256'])
        self.assertEqual(set(x['case_id'] for x in cases), {row['case_id'] for row in summary['rows']})

    def test_duplicate_summary_attempt_rejects_before_output_creation(self):
        suite, run, qualification, frozen, _ = self._mini_inputs(duplicate_attempt=True)
        output = self.root / 'rejected'
        with patch.object(adapter, 'verify_suite', return_value=frozen), \
             patch.object(adapter, 'qualification_ok', return_value=True), \
             patch.object(adapter, 'review_testbench') as review:
            with self.assertRaises(ValueError):
                adapter.replay(suite, [run], output, qualification)
        self.assertFalse(output.exists())
        review.assert_not_called()
if __name__ == '__main__':
    unittest.main()
