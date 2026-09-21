"""Register an already-built fix-card corpus and index as reference-only."""
import argparse
from pathlib import Path

from rag.common import file_sha256, read_json, write_json


ROOT = Path(__file__).resolve().parents[1]


def register(corpus, index, output, release_id='ug1399-2026.1-fix-cards-v1'):
    corpus = Path(corpus).resolve()
    index = Path(index).resolve()
    output = Path(output).resolve()
    if not (corpus / 'manifest.json').is_file() or not (corpus / 'records.jsonl').is_file():
        raise FileNotFoundError('Incomplete corpus: ' + str(corpus))
    if not (index / 'manifest.json').is_file() or not (index / 'vectors.npy').is_file():
        raise FileNotFoundError('Incomplete index: ' + str(index))
    cm = read_json(corpus / 'manifest.json')
    im = read_json(index / 'manifest.json')
    if im.get('corpus_sha256') != cm.get('records_sha256'):
        raise ValueError('Index is not bound to this corpus')
    if cm.get('profile') != 'fix-cards-v1' or cm.get('corpus_class') != 'fix':
        raise ValueError('Expected the fix-card corpus profile and class')
    card_count = cm.get('records', 0)
    data = dict(
        schema_version=1, release_id=release_id, status='reference',
        corpus='rag/corpora/' + corpus.name,
        index='rag/indexes/' + index.name,
        corpus_manifest_sha256=file_sha256(corpus / 'manifest.json'),
        records_sha256=file_sha256(corpus / 'records.jsonl'),
        index_manifest_sha256=file_sha256(index / 'manifest.json'),
        index_fingerprint=im['fingerprint'],
        vectors_sha256=file_sha256(index / 'vectors.npy'),
        source_version='2026.1', profile='fix-cards-v1',
        corpus_class='fix', validation_status='human_reviewed',
        note=(f'{card_count} human-reviewed, UG1399-page-anchored repair cards. Reference-only: '
              'human_reviewed does not mean compile- or synthesis-validated.'),
    )
    write_json(output, data)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', default='rag/corpora/ug1399-2026.1-fix-cards-v1')
    parser.add_argument('--index', default='rag/indexes/ug1399-qwen06b-2026.1-fix-cards-v1')
    parser.add_argument('--output', default='rag/releases/ug1399-2026.1-fix-cards-v1.json')
    args = parser.parse_args()
    print(register(ROOT / args.corpus, ROOT / args.index, ROOT / args.output))


if __name__ == '__main__':
    main()
