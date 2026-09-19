"""A pinned, explicitly selected reference release; never discover staging data."""
from pathlib import Path

from rag.common import canonical, file_sha256, read_json, sha256

ROOT = Path(__file__).resolve().parents[1]


def resolve(value):
    path = Path(value)
    return (ROOT / path).resolve() if not path.is_absolute() else path.resolve()


def load_release(path):
    path = resolve(path)
    data = read_json(path)
    required = {'schema_version', 'release_id', 'status', 'corpus', 'index',
                'corpus_manifest_sha256', 'records_sha256', 'index_manifest_sha256',
                'index_fingerprint', 'vectors_sha256'}
    if not required.issubset(data) or data['schema_version'] != 1 or data['status'] != 'reference':
        raise ValueError('Expected an explicitly approved reference release')
    for key, folder in (('corpus', 'corpora'), ('index', 'indexes')):
        target = resolve(data[key])
        if not target.is_relative_to((ROOT / 'rag' / folder).resolve()):
            raise ValueError('Release path must stay in rag/' + folder + '; staging is not a release')
        data[key] = str(target)
    return data


def release_files(release):
    corpus, index = Path(release['corpus']), Path(release['index'])
    return {
        corpus / 'manifest.json': release['corpus_manifest_sha256'],
        corpus / 'records.jsonl': release['records_sha256'],
        index / 'manifest.json': release['index_manifest_sha256'],
        index / 'vectors.npy': release['vectors_sha256'],
    }


def verify_release(release):
    for path, expected in release_files(release).items():
        if file_sha256(path) != expected:
            raise ValueError('Released artifact changed: ' + str(path))
    corpus = read_json(Path(release['corpus']) / 'manifest.json')
    index = read_json(Path(release['index']) / 'manifest.json')
    if (corpus['records_sha256'] != release['records_sha256']
            or index['corpus_sha256'] != release['records_sha256']
            or index['fingerprint'] != release['index_fingerprint']
            or index['vectors_sha256'] != release['vectors_sha256']):
        raise ValueError('Release corpus/index bindings differ')


def fingerprint(release):
    return sha256(canonical(release))


def reference_records(records):
    for record in records:
        if record['release']['status'] != 'reference' or record['role'] not in {'doc', 'design'}:
            raise ValueError('Release contains pending/withdrawn material or a non-reference role')
