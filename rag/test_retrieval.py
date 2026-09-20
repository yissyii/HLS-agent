"""Offline contract checks; no model download, generation, or Vitis invocation."""
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from rag.common import record, write_corpus, load_corpus, read_json, write_json, sha256
from rag.ingest_pdf import split_text
from rag.retrieve import Retriever, tokenize, rrf


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parent / '.cache' / 'tests'
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp_root = temp_root
        self.tmp_path = temp_root / uuid.uuid4().hex
        self.tmp_path.mkdir()
        self.path = self.tmp_path / 'corpus'
        def source(page_start, page_end, parent_id):
            return dict(document='UG1399', version='2025.2', language='en-US',
                        file='manual.pdf', file_sha256='0' * 64, page_start=page_start,
                        page_end=page_end, section_path=['Top', 'Child'], parent_id=parent_id,
                        license='test-license')

        self.records = [
            record('a', 'pragma HLS array_partition', 'Partition array into independent banks.',
                   source(10, 10, 'p1'), kind='document_section', release={'status': 'reference'}),
            record('b', 'pragma HLS array_partition', 'Cyclic banks use round robin assignment.',
                   source(11, 11, 'p1'), kind='document_section', release={'status': 'reference'}),
            record('c', 'Dynamic Memory Usage', 'malloc and free are not synthesizable.',
                   source(20, 20, 'p2'), kind='document_section', release={'status': 'reference'}),
        ]
        self.records[0].update(corpus_class='general', corpus_priority=50)
        self.records[1].update(corpus_class='general', corpus_priority=50)
        self.records[2].update(corpus_class='fix', corpus_priority=100)
        write_corpus(self.path, self.records, {})

    def tearDown(self):
        assert self.tmp_path.resolve().is_relative_to(self.temp_root.resolve())
        shutil.rmtree(self.tmp_path)

    def test_symbols_and_chinese_tokens(self):
        tokens = tokenize('hls::stream array_partition 数组分区')
        for token in ('hls::stream', 'stream', 'array_partition', 'partition', '数组', '分区'):
            self.assertIn(token, tokens)

    def test_budget_and_provenance(self):
        retrieval = Retriever(self.path)
        result = retrieval.search('malloc', mode='bm25', max_bytes=2000)
        self.assertEqual(result['hits'][0]['record']['id'], 'c')
        context = ''.join(h['context'] for h in result['hits'])
        self.assertEqual(result['context_bytes'], len(context.encode('utf-8')))
        self.assertIn('pp.20-20', context)
        self.assertIn('release=reference', context)
        self.assertIn('validation=unvalidated', context)
        self.assertEqual(retrieval.search('malloc', mode='bm25', max_bytes=10)['hits'], [])

    def test_parent_diversity(self):
        result = Retriever(self.path).search('array_partition banks', mode='bm25')
        self.assertEqual(len(result['hits']), 1)

    def test_profile_filter_and_fix_first_metadata(self):
        retrieval = Retriever(self.path)
        fix = retrieval.search('malloc', mode='bm25', profile='fix_first')
        self.assertEqual(fix['hits'][0]['record']['corpus_class'], 'fix')
        self.assertEqual(fix['profile'], 'fix_first')
        general = retrieval.search('malloc', mode='bm25', profile='general_only')
        self.assertEqual(general['hits'], [])

    def test_reranker_can_overturn_profile_prior(self):
        class FakeReranker:
            identity = {'repository': 'test-reranker'}

            def score(self, query, passages):
                return [0.1 if 'malloc' in passage else 0.9 for passage in passages]

        result = Retriever(self.path).search('malloc array_partition', mode='bm25', top_k=2,
                                             max_bytes=4000, profile='fix_first',
                                             reranker=FakeReranker(), rerank_k=3)
        self.assertEqual(result['hits'][0]['record']['corpus_class'], 'general')
        self.assertEqual(result['reranker']['repository'], 'test-reranker')

    def test_corpus_tampering_rejected(self):
        with (self.path / 'records.jsonl').open('ab') as stream:
            stream.write(b' ')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            load_corpus(self.path)

    def test_inner_content_tampering_rejected(self):
        payload = (self.path / 'records.jsonl').read_bytes().replace(b'malloc', b'calloc')
        (self.path / 'records.jsonl').write_bytes(payload)
        manifest = read_json(self.path / 'manifest.json')
        manifest['records_sha256'] = sha256(payload)
        write_json(self.path / 'manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'content checksum'):
            load_corpus(self.path)

    def test_rrf_agreement(self):
        fused = rrf([[(0, 100), (1, 1)], [(1, .9), (2, .8)]])
        self.assertEqual(fused[0][0], 1)

    def test_missing_dense_index_fails_explicitly(self):
        with self.assertRaisesRegex(ValueError, 'vector index'):
            Retriever(self.path).search('malloc', mode='hybrid')

    def test_chunking_preserves_lines_and_flags(self):
        text = 'line one\nline two\nline three'
        parts = split_text(text, max_chars=12)
        self.assertEqual('\n'.join(p for p, _ in parts), text)
        self.assertTrue(all('oversized_block_split' in flags for _, flags in parts))
        self.assertEqual(split_text('x' * 30, max_chars=12)[0][0], 'x' * 30)

    def test_dense_index_integrity(self):
        import numpy as np
        index = self.tmp_path / 'index'
        index.mkdir()
        vectors = np.zeros((3, 1024), dtype=np.float32)
        vectors[:, 0] = 1
        np.save(index / 'vectors.npy', vectors)
        manifest = dict(corpus_sha256=read_json(self.path / 'manifest.json')['records_sha256'],
                        record_ids=[r['id'] for r in self.records],
                        vectors_sha256=sha256((index / 'vectors.npy').read_bytes()))
        write_json(index / 'manifest.json', manifest)
        Retriever(self.path, index)
        with (index / 'vectors.npy').open('ab') as stream:
            stream.write(b'corruption')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            Retriever(self.path, index)

    def test_interrupted_build_resumes_without_losing_completed_rows(self):
        import numpy as np
        from rag.build_index import build

        class FakeEncoder:
            identity = {'model': 'test-model'}
            max_length = 2048
            calls = 0
            fail_on = 2

            def lengths(self, texts):
                return [len(t) for t in texts]

            def encode(self, texts, **kwargs):
                self.calls += 1
                if self.calls == self.fail_on:
                    raise RuntimeError('injected interruption')
                vectors = np.zeros((len(texts), 1024), dtype=np.float32)
                for i, text in enumerate(texts):
                    vectors[i, len(text) % 1024] = 1
                return vectors

        index = self.tmp_path / 'resumed-index'
        encoder = FakeEncoder()
        with patch('rag.build_index.QwenEncoder', return_value=encoder):
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                build(self.path, index, 'unused', batch_size=1)
            first_done = read_json(index / 'build-state.json')['completed_rows']
            self.assertEqual(len(first_done), 1)
            first_vector = np.load(index / 'vectors.npy')[first_done[0]].copy()
            encoder.fail_on = None
            encoder.calls = 0
            build(self.path, index, 'unused', batch_size=1)
            self.assertEqual(encoder.calls, 2)
            self.assertTrue(np.array_equal(np.load(index / 'vectors.npy')[first_done[0]], first_vector))
            Retriever(self.path, index)
            with self.assertRaisesRegex(FileExistsError, 'Completed index'):
                build(self.path, index, 'unused', batch_size=1)


if __name__ == '__main__':
    unittest.main()
