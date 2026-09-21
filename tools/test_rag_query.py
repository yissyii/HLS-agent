"""Diagnostic-only gates and selection before Top-k; no embedding dependency."""
import unittest

from rag.query import query_plan, assess, clean_feedback
from rag.retrieve import Retriever, BM25


class QueryContracts(unittest.TestCase):
    def plan(self, text, category='compile_error', policy='compiler_diagnostics'):
        return query_plan('Long task ' * 300, text, category, policy, 1600)

    def test_category_only_cannot_become_a_task_only_search(self):
        for text in ('functional_or_runtime_error', 'error: functional_or_runtime_error'):
            _, plan = self.plan(text, 'functional_or_runtime_error')
            self.assertEqual(plan['skip_reason'], 'insufficient_diagnostic')
        _, plan = self.plan('csim: compile_error. Detailed diagnostics are not released by this task.', policy='category_only')
        self.assertEqual(plan['skip_reason'], 'insufficient_diagnostic')

    def test_query_prioritizes_diagnostic_and_strips_locations(self):
        error = "/tmp/run42/input/kernel.cpp:12:3: error: no member 'parity' in 'ap_uint<100>'"
        query, plan = self.plan(error + '\n' + error)
        self.assertLessEqual(len(query), 1600)
        self.assertEqual(query.count("no member 'parity'"), 1)
        self.assertNotIn('run42', query)
        self.assertIn('ap_uint<100>', query)
        self.assertEqual(plan['required_terms'], ['parity'])
        self.assertTrue(plan['problem_trimmed'])
        self.assertFalse(plan['skip_reason'])

    def test_member_and_second_error_term_required(self):
        _, plan = self.plan("error: no member 'parity' in 'ap_uint<100>'")
        def check(text): return assess(dict(id='a', title='Methods', text=text), plan)
        self.assertFalse(check('ap_uint general methods')[0])
        self.assertFalse(check('parity')[0])
        self.assertTrue(check('ap_uint bitwise parity reduction')[0])

    def test_filter_is_before_top_k(self):
        _, plan = self.plan("error: no member 'parity' in 'ap_uint<100>'")
        records = [dict(id='a', title='ap_uint', text='ap_uint types', source={}, content_sha256='a'),
                   dict(id='b', title='parity', text='ap_uint parity reduction', source={}, content_sha256='b')]
        retriever = Retriever.__new__(Retriever)
        retriever.records = records
        retriever.bm25 = BM25(records)
        retriever.bm25.search = lambda *args: [(0, 10), (1, 5)]
        retriever.corpus_manifest = {'records_sha256': 'fixture'}
        retriever.index_manifest = None
        retriever.render = lambda r: r['text']
        result = retriever.search('parity', mode='bm25', top_k=1,
                                  evidence_filter=lambda r: assess(r, plan))
        self.assertEqual([h['record']['id'] for h in result['hits']], ['b'])
        self.assertFalse(result['selection_audit'][0]['accepted'])

    def test_public_feedback_remains_authorized_but_not_invented(self):
        query, plan = self.plan('Mismatch cycle 4: expected 7 got 3', 'functional_or_runtime_error', 'public_diagnostics')
        self.assertFalse(plan['skip_reason'])
        self.assertIn('expected 7 got 3', query)

    def test_provisional_annotation_distinguishes_generic_diagnostic(self):
        _, plan = self.plan("error: use of undeclared identifier 'reset'", 'compile_error')
        self.assertEqual(plan['annotation']['status'], 'provisional')
        self.assertEqual(plan['annotation']['signal_class'], 'ambiguous')
        self.assertEqual(plan['annotation']['recommended_action'], 'judge')
        self.assertIsNone(plan['annotation']['human_label'])

    def test_provisional_annotation_records_specific_code_and_location(self):
        _, plan = self.plan('/tmp/kernel.cpp:12:3: error: [HLS 200-101] invalid option\n#pragma HLS pipeline\n  ^')
        annotation = plan['annotation']
        self.assertEqual(annotation['signal_class'], 'specific')
        self.assertGreaterEqual(annotation['diagnostic_quality_score'], 4)
        self.assertTrue(annotation['features']['has_location'])
        self.assertTrue(annotation['features']['has_source_line'])

    def test_selection_audit_contains_provisional_candidate_annotation(self):
        _, plan = self.plan("error: no member 'parity' in 'ap_uint<100>'")
        accepted, decision = assess(dict(id='a', title='parity', text='ap_uint parity reduction'), plan)
        self.assertTrue(accepted)
        self.assertEqual(decision['annotation']['status'], 'provisional')
        self.assertEqual(decision['annotation']['record_id'], 'a')


if __name__ == '__main__': unittest.main()
