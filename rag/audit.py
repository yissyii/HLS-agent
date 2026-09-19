"""Record reproducible local corpus/index/model evidence without publishing full text."""
import argparse
from collections import Counter
from pathlib import Path

from rag.common import canonical, file_sha256, read_json, write_json, sha256
from rag.retrieve import Retriever


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--corpus', default='rag/corpora/ug1399-2025.2-en')
    p.add_argument('--index', default='rag/indexes/ug1399-qwen06b-en')
    p.add_argument('--model', default='F:/Workspace/hls-rag/models/Qwen3-Embedding-0.6B')
    p.add_argument('--pdf', default='../docs/ug1399-vitis-hls-en-us-2025.2.pdf')
    p.add_argument('--output', required=True)
    a = p.parse_args()
    if Path(a.output).exists():
        raise FileExistsError('Use a new evidence output path')
    retrieval = Retriever(a.corpus, a.index)
    import numpy as np
    if not np.allclose(np.linalg.norm(retrieval.vectors, axis=1), 1, atol=1e-4):
        raise ValueError('Index vectors are not normalized')
    source = retrieval.corpus_manifest
    if file_sha256(a.pdf) != source['source_sha256']:
        raise ValueError('Source PDF changed')
    model = read_json(Path(a.model) / 'rag-model-manifest.json')
    if sha256(canonical(model)) != retrieval.index_manifest['encoder']['model_manifest_sha256']:
        raise ValueError('Index was built using a different model manifest')
    root = Path(a.model).resolve()
    for name, item in model['files'].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or file_sha256(path) != item['sha256']:
            raise ValueError('Model file changed: ' + name)
    coverage = read_json(Path(a.corpus) / 'coverage.json')
    records = retrieval.records
    sections = read_json(Path(a.corpus) / 'sections.json')
    for r in records:
        s = r['source']
        if (s['file_sha256'] != source['source_sha256'] or s['language'] != 'en-US'
                or s['version'] != '2025.2' or s['parent_id'] not in sections
                or not 1 <= s['page_start'] <= s['page_end'] <= source['source_pages']):
            raise ValueError('Invalid record provenance: ' + r['id'])
    report = dict(
        corpus=source, model=model,
        index={k: v for k, v in retrieval.index_manifest.items() if k != 'record_ids'},
        source_pages_with_chunks=len({r['source']['page_start'] for r in records}),
        pages_without_section_text=[p['page'] for p in coverage if not p['section_chars']],
        quality_counts=dict(Counter(flag for r in records for flag in r['quality'])),
        manual_visual_spot_checks={
            '125': 'Statics code and initialization/reset text checked against rendered page.',
            '129': 'Table 5 AP types and required headers checked; layout text, not structured cells.',
            '154': 'Pointer limitations and recursive function example checked against page.',
            '547': 'array_partition pragma syntax checked; remaining options continue on following page.',
        },
        corpus_auxiliary_sha256={path.name: file_sha256(path) for path in Path(a.corpus).glob('*.json')},
        build_code_sha256={path.name: file_sha256(path) for path in Path(__file__).parent.glob('*.py')},
        dependency_lock_sha256=file_sha256(Path(__file__).parent / 'requirements.lock.txt'),
        caveats=['Spot checks are not exhaustive PDF QA.', 'Figures are not OCR-indexed.',
                 'No remote Vitis run or Agent success-rate experiment was performed.'],
    )
    write_json(a.output, report)
    print('Verified corpus, source PDF, local model files, and normalized index:', a.output)


if __name__ == '__main__':
    main()
