"""Contract checks for the small source-anchored UG1399 fix-card release."""
import unittest
from pathlib import Path

from rag.common import load_corpus
from rag.release import load_release, reference_records, verify_release
from rag.retrieve import Retriever


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / 'rag' / 'corpora' / 'ug1399-2026.1-fix-cards-v1'
RELEASE = ROOT / 'rag' / 'releases' / 'ug1399-2026.1-fix-cards-v1.json'


class FixCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, cls.manifest = load_corpus(CORPUS)

    def test_small_source_anchored_release(self):
        self.assertEqual(len(self.records), 18)
        self.assertEqual(self.manifest['profile'], 'fix-cards-v1')
        self.assertEqual(self.manifest['source_sha256'],
                         '7da1b9ffdca0b259d96781c25a3acd222429556daf269cf847e3993679abf6cf')
        for item in self.records:
            self.assertEqual(item['kind'], 'fix_card')
            self.assertEqual(item['corpus_class'], 'fix')
            self.assertEqual(item['validation']['status'], 'human_reviewed')
            self.assertTrue(item['source']['citation'].startswith('UG1399 v2026.1'))
            self.assertTrue(item['source_citation'].startswith('UG1399 v2026.1'))
            self.assertTrue(item['required_constructs'] or item['signature_terms'])
            self.assertTrue(item['exclusions'] is not None)

    def test_release_hashes_and_roles(self):
        release = load_release(RELEASE)
        verify_release(release)
        reference_records(self.records)
        self.assertEqual(release['release_id'], 'ug1399-2026.1-fix-cards-v1')
        self.assertEqual(release['validation_status'], 'human_reviewed')

    def test_bm25_returns_structured_fix_card(self):
        retriever = Retriever(CORPUS)
        result = retriever.search('SCHED 204-69 limited memory ports array partition',
                                  mode='bm25', top_k=1, max_bytes=4000)
        hit = result['hits'][0]['record']
        self.assertEqual(hit['id'], 'fix-ug1399-2026.1-array-partition-memory-ports')
        self.assertIn('Action:', result['hits'][0]['context'])
        self.assertIn('Applicability:', result['hits'][0]['context'])
        self.assertIn('Exclusions:', result['hits'][0]['context'])


if __name__ == '__main__':
    unittest.main()
