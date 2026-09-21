"""Offline retrieval evaluation; does not invoke the generation model or Vitis."""
import argparse
from collections import defaultdict
import re
import time

from rag.common import read_json, write_json, file_sha256
from rag.retrieve import Retriever


def normalize_title(value):
    """Make gold labels stable across minor heading punctuation/case changes."""
    return re.sub(r'[^a-z0-9]+', '', value.lower())


def matches_gold(title, gold_titles):
    normalized = normalize_title(title)
    return any(normalized == normalize_title(gold)
               or normalized.endswith(normalize_title(gold))
               or normalize_title(gold).endswith(normalized)
               for gold in gold_titles)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--corpus', default='rag/corpora/ug1399-2026.1-en-curated')
    p.add_argument('--index', default='rag/indexes/ug1399-qwen06b-2026.1-curated')
    p.add_argument('--model', default='F:/Workspace/hls-rag/models/Qwen3-Embedding-0.6B')
    p.add_argument('--reranker', help='Optional local Qwen3-Reranker-0.6B path')
    p.add_argument('--rerank-k', type=int, default=5)
    p.add_argument('--limit', type=int, help='Evaluate only the first N queries for a smoke run')
    p.add_argument('--queries', default='rag/eval_queries.json')
    p.add_argument('--modes', nargs='+', choices=['bm25', 'dense', 'hybrid'], default=['bm25', 'dense', 'hybrid'])
    p.add_argument('--top-k', type=int, default=5)
    p.add_argument('--recall-k', type=int, default=30)
    p.add_argument('--max-bytes', type=int, default=20000)
    p.add_argument('--output', required=True)
    a = p.parse_args()
    if a.top_k < 1 or a.recall_k < a.top_k or a.max_bytes < 1 or a.rerank_k < a.top_k:
        p.error('Invalid retrieval limits')
    from pathlib import Path
    if Path(a.output).exists():
        raise FileExistsError('Evaluation output exists; use a new path')
    data = read_json(a.queries)
    if a.limit is not None:
        if a.limit < 1:
            p.error('--limit must be positive')
        data = dict(data, queries=data['queries'][:a.limit])
    retriever = Retriever(a.corpus, a.index if any(m != 'bm25' for m in a.modes) else None, a.model)
    reranker = None
    if a.reranker:
        from rag.reranker import QwenReranker
        reranker = QwenReranker(a.reranker)
    titles = {normalize_title(r['title']) for r in retriever.records}
    for q in data['queries']:
        if not any(any(item == normalize_title(t)
                       or item.endswith(normalize_title(t))
                       or normalize_title(t).endswith(item) for item in titles)
                   for t in q['titles']):
            raise ValueError('No gold section exists for ' + q['id'])
    vectors = None
    encoding_time = None
    if any(m != 'bm25' for m in a.modes):
        started = time.monotonic()
        vectors = retriever.encode_queries([q['query'] for q in data['queries']])
        encoding_time = time.monotonic() - started
    runs, summary = [], {}
    for mode in a.modes:
        group = defaultdict(list)
        for i, q in enumerate(data['queries']):
            result = retriever.search(q['query'], mode=mode, top_k=a.top_k, recall_k=a.recall_k, max_bytes=a.max_bytes,
                                      query_vector=vectors[i] if vectors is not None else None,
                                      reranker=reranker, rerank_k=min(a.rerank_k, a.recall_k))
            flags = [matches_gold(h['record']['title'], q['titles']) for h in result['hits']]
            first = next((n for n, flag in enumerate(flags, 1) if flag), None)
            item = dict(query_id=q['id'], kind=q['kind'], mode=mode, gold_titles=q['titles'],
                        first_relevant_rank=first, hit_at_1=bool(first == 1),
                        hit_at_3=bool(first and first <= 3), hit_at_5=bool(first and first <= 5),
                        retrieved=[{'id': h['record']['id'], 'title': h['record']['title'],
                                    'page': h['record']['source']['page_start'], 'score': h['score']} for h in result['hits']],
                        context_bytes=result['context_bytes'], search_seconds=result['elapsed_seconds'])
            runs.append(item)
            group['all'].append(item)
            group[q['kind']].append(item)
        summary[mode] = {key: dict(queries=len(items), **{m: sum(x[m] for x in items) / len(items)
                         for m in ('hit_at_1', 'hit_at_3', 'hit_at_5') if int(m.rsplit('_', 1)[1]) <= a.top_k}) for key, items in group.items()}
    report = dict(description=data['description'], corpus_sha256=retriever.corpus_manifest['records_sha256'],
                  queries_sha256=file_sha256(a.queries),
                  configuration=dict(top_k=a.top_k, recall_k=a.recall_k, max_bytes=a.max_bytes,
                                     reranker=reranker.identity if reranker else None,
                                     rerank_k=a.rerank_k),
                  index_fingerprint=retriever.index_manifest['fingerprint'] if retriever.index_manifest else None,
                  query_encoding_and_cold_load_seconds=encoding_time, summary=summary, runs=runs,
                  limitations=['Author-curated small development smoke set; not independent hold-out.',
                               'Gold labels are at section level; a retrieved chunk may not contain all required details.',
                               'These are retrieval hit rates, not HLS correctness or Agent repair gains.',
                               'Search timing excludes cached query encoding; cold-load time reported separately.'])
    write_json(a.output, report)
    print(summary)


if __name__ == '__main__':
    main()
