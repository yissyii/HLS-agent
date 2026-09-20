"""BM25 + Qwen dense retrieval, RRF fusion, provenance and bounded context."""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import re
import time
import unicodedata

from rag.common import load_corpus, read_json, sha256

STOP_WORDS = set('a an the is are was were be been being to of for from in on at by with and or as it its '
                 'this that these those how what when which can could should would do does did have has '
                 'i my we our you your use using used'.split())


def tokenize(text):
    text = unicodedata.normalize('NFKC', text).lower()
    result = []
    for match in re.finditer(r'[a-z_][a-z0-9_]*(?:::[a-z_][a-z0-9_]*)*|\d+(?:\.\d+)*|[\u4e00-\u9fff]+', text):
        word = match.group()
        if '\u4e00' <= word[0] <= '\u9fff':
            result.extend(word[i:i + 2] for i in range(max(1, len(word) - 1)))
        elif word not in STOP_WORDS:
            result.append(word)
            if '_' in word or '::' in word:
                result.extend(w for w in re.split(r'_|::', word) if len(w) > 1 and w not in STOP_WORDS)
    return result


class BM25:
    def __init__(self, records, k1=1.5, b=0.75):
        self.records, self.k1, self.b = records, k1, b
        self.postings = defaultdict(dict)
        self.lengths = []
        for i, r in enumerate(records):
            text = r['title'] + ' ' + r['title'] + ' ' + ' '.join(r.get('aliases', [])) + ' ' + r['text']
            counts = Counter(tokenize(text))
            self.lengths.append(sum(counts.values()))
            for token, count in counts.items():
                self.postings[token][i] = count
        self.avg_length = sum(self.lengths) / max(1, len(records))

    def search(self, query, limit=20):
        scores = defaultdict(float)
        count = len(self.records)
        for token in set(tokenize(query)):
            posting = self.postings.get(token, {})
            idf = math.log(1 + (count - len(posting) + 0.5) / (len(posting) + 0.5))
            for i, frequency in posting.items():
                denominator = frequency + self.k1 * (1 - self.b + self.b * self.lengths[i] / max(self.avg_length, 1))
                scores[i] += idf * frequency * (self.k1 + 1) / denominator
        return sorted(scores.items(), key=lambda p: (-p[1], self.records[p[0]]['id']))[:limit]


def rrf(rankings, constant=60):
    scores = defaultdict(float)
    for ranking in rankings:
        for rank, (i, _) in enumerate(ranking, 1):
            scores[i] += 1.0 / (constant + rank)
    return sorted(scores.items(), key=lambda p: (-p[1], p[0]))


class Retriever:
    def __init__(self, corpus, index=None, model=None, threads=6):
        self.records, self.corpus_manifest = load_corpus(corpus)
        self.bm25 = BM25(self.records)
        self.index_manifest = None
        self.vectors = None
        self.encoder = None
        self.model_path = model
        self.threads = threads
        self.corpus_path = Path(corpus)
        if index:
            import numpy as np
            self.index_manifest = read_json(Path(index) / 'manifest.json')
            m = self.index_manifest
            if m['corpus_sha256'] != self.corpus_manifest['records_sha256'] or m['record_ids'] != [r['id'] for r in self.records]:
                raise ValueError('Dense index does not match corpus')
            path = Path(index) / 'vectors.npy'
            if sha256(path.read_bytes()) != m['vectors_sha256']:
                raise ValueError('Dense index checksum mismatch')
            self.vectors = np.load(path, mmap_mode='r', allow_pickle=False)
            if self.vectors.shape != (len(self.records), 1024) or not np.isfinite(self.vectors).all():
                raise ValueError('Invalid dense index shape/values')

    def encode_queries(self, queries):
        if self.vectors is None or not self.model_path:
            raise ValueError('Dense/hybrid retrieval requires a completed index and local model')
        if self.encoder is None:
            from rag.embedding import QwenEncoder
            self.encoder = QwenEncoder(self.model_path, self.threads)
            if self.encoder.identity != self.index_manifest['encoder']:
                raise ValueError('Query encoder differs from index encoder; rebuild or explicitly validate migration')
        return self.encoder.encode(queries, query=True)

    def search(self, query, mode='hybrid', top_k=3, recall_k=20, max_bytes=6000,
               query_vector=None, diversify=True, evidence_filter=None):
        if not query.strip() or top_k < 1 or recall_k < top_k or max_bytes < 1:
            raise ValueError('Nonempty query and valid positive retrieval limits required')
        if mode not in ('bm25', 'dense', 'hybrid'):
            raise ValueError('Unknown retrieval mode')
        started = time.monotonic()
        sparse = self.bm25.search(query, recall_k) if mode != 'dense' else []
        dense = []
        if mode != 'bm25':
            import numpy as np
            if self.vectors is None:
                raise ValueError('No completed vector index')
            vector = query_vector if query_vector is not None else self.encode_queries([query])[0]
            if np.asarray(vector).shape != (1024,) or not np.isfinite(vector).all():
                raise ValueError('Invalid query vector')
            similarities = self.vectors @ vector
            order = np.argsort(-similarities, kind='stable')[:recall_k]
            dense = [(int(i), float(similarities[i])) for i in order]
        ranking = sparse if mode == 'bm25' else dense if mode == 'dense' else rrf([sparse, dense])
        sparse_scores, dense_scores = dict(sparse), dict(dense)
        hits, parents, hashes = [], set(), set()
        used = 0
        selection_audit = []
        for row, score in ranking:
            r = self.records[row]
            if evidence_filter:
                accepted, decision = evidence_filter(r)
                selection_audit.append(decision)
                if not accepted:
                    continue
            parent = r['source'].get('parent_id', r['id'])
            if r['content_sha256'] in hashes or (diversify and parent in parents):
                continue
            block = self.render(r)
            size = len(block.encode('utf-8'))
            if used + size > max_bytes:
                continue  # Never cut a rule or code fragment to squeeze it into budget.
            hits.append(dict(record=r, score=score, bm25_score=sparse_scores.get(row),
                             cosine=dense_scores.get(row), context=block, bytes=size))
            used += size
            parents.add(parent)
            hashes.add(r['content_sha256'])
            if len(hits) == top_k:
                break
        return dict(mode=mode, query=query, corpus_sha256=self.corpus_manifest['records_sha256'],
                    index_fingerprint=self.index_manifest['fingerprint'] if self.index_manifest and mode != 'bm25' else None,
                    hits=hits, selection_audit=selection_audit,
                    context_bytes=used, max_bytes=max_bytes, token_count_verified=False,
                    budget_method='UTF-8 byte cap; not an exact generation-model token count',
                    elapsed_seconds=round(time.monotonic() - started, 4),
                    caution='Ranked reference candidates, not verified repairs or a calibrated confidence score.')

    @staticmethod
    def render(record):
        s = record['source']
        if record['kind'] == 'document_section':
            location = (f'{s.get("document", "?")} {s.get("version", "?")} {s.get("language", "?")}; '
                        f'{s.get("file", "?")} pp.{s.get("page_start", "?")}-{s.get("page_end", "?")}')
            caution = 'PDF text may be a fragment, not a complete compilable example.'
        else:
            location = s.get('repository', s.get('file', '?'))
            caution = 'Check applicability and preserve the task interface/behavior.'
        return (f'[REFERENCE {record["id"]}] {record["title"]}\n'
                f'Source: {location}; tool={record["tool"]["target_version"]}; '
                f'release={record["release"]["status"]}; validation={record["validation"]["status"]}\n'
                + caution + '\n'
                + record['text'] + '\n[/REFERENCE]\n')

    def parent(self, identifier):
        record = next(r for r in self.records if r['id'] == identifier)
        parent_id = record['source']['parent_id']
        return read_json(self.corpus_path / 'sections.json')[parent_id]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('query', nargs='?', default='')
    p.add_argument('--corpus', default='rag/corpora/ug1399-2025.2-en')
    p.add_argument('--index', default='rag/indexes/ug1399-qwen06b-en')
    p.add_argument('--model', default='F:/Workspace/hls-rag/models/Qwen3-Embedding-0.6B')
    p.add_argument('--mode', choices=['bm25', 'dense', 'hybrid'], default='hybrid')
    p.add_argument('--top-k', type=int, default=3)
    p.add_argument('--max-bytes', type=int, default=6000)
    p.add_argument('--expand', help='Return a full parent section by record ID, for inspection only')
    p.add_argument('--output')
    a = p.parse_args()
    retriever = Retriever(a.corpus, a.index if a.mode != 'bm25' and not a.expand else None, a.model)
    result = retriever.parent(a.expand) if a.expand else retriever.search(a.query, a.mode, a.top_k, max_bytes=a.max_bytes)
    if a.output:
        from rag.common import write_json
        write_json(a.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
