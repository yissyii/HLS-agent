"""Build/resume a checksum-bound dense index; each corpus gets an immutable index."""
import argparse
from pathlib import Path
import time

from rag.common import canonical, load_corpus, read_json, sha256, write_json
from rag.embedding import QwenEncoder, document_text


def build(corpus, output, model, batch_size=8, threads=6):
    import numpy as np

    records, corpus_manifest = load_corpus(corpus)
    encoder = QwenEncoder(model, threads=threads)
    output = Path(output)
    identity = dict(corpus_sha256=corpus_manifest['records_sha256'], encoder=encoder.identity,
                    record_ids=[r['id'] for r in records])
    fingerprint = sha256(canonical(identity))
    state_path = output / 'build-state.json'
    if output.exists():
        if (output / 'manifest.json').exists():
            raise FileExistsError('Completed index exists; do not overwrite experiment evidence')
        state = read_json(state_path)
        if state['fingerprint'] != fingerprint:
            raise ValueError('Partial index does not match corpus/model/configuration')
        vectors = np.lib.format.open_memmap(output / 'vectors.npy', mode='r+')
        done = set(state['completed_rows'])
    else:
        output.mkdir(parents=True)
        vectors = np.lib.format.open_memmap(output / 'vectors.npy', mode='w+', dtype=np.float32,
                                          shape=(len(records), 1024))
        vectors[:] = np.nan
        vectors.flush()
        done = set()
        write_json(state_path, {'fingerprint': fingerprint, 'completed_rows': []})
    texts = [document_text(r) for r in records]
    lengths = encoder.lengths(texts)
    if max(lengths) > encoder.max_length:
        raise ValueError('Corpus contains oversized embedding input')
    order = sorted((i for i in range(len(records)) if i not in done), key=lambda i: lengths[i])
    start = time.monotonic()
    initial_done = len(done)
    for offset in range(0, len(order), batch_size):
        rows = order[offset:offset + batch_size]
        result = encoder.encode([texts[i] for i in rows], batch_size=batch_size)
        vectors[rows] = result
        vectors.flush()
        done.update(rows)
        # Atomic progress file; vectors flushed before recording completion.
        temporary = state_path.with_suffix('.tmp')
        write_json(temporary, {'fingerprint': fingerprint, 'completed_rows': sorted(done)})
        temporary.replace(state_path)
        elapsed = time.monotonic() - start
        speed = (len(done) - initial_done) / max(elapsed, 0.001)
        print(f'Embedded {len(done)}/{len(records)}; {elapsed:.1f}s; {speed:.2f} chunks/s', flush=True)
    if not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-4):
        raise ValueError('Invalid or incomplete vector index')
    vectors.flush()
    manifest = dict(schema_version=1, **identity, fingerprint=fingerprint,
                    vectors_sha256=sha256((output / 'vectors.npy').read_bytes()),
                    rows=len(records), dimensions=1024, max_document_tokens=max(lengths),
                    total_document_tokens=sum(lengths), this_run_seconds=round(time.monotonic() - start, 3))
    write_json(output / 'manifest.json', manifest)
    print('Completed index:', output, flush=True)
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('corpus')
    p.add_argument('output')
    p.add_argument('--model', default='F:/Workspace/hls-rag/models/Qwen3-Embedding-0.6B')
    p.add_argument('--batch-size', type=int, default=8)
    p.add_argument('--threads', type=int, default=6)
    a = p.parse_args()
    if a.batch_size < 1 or a.threads < 1:
        p.error('batch-size and threads must be positive')
    build(a.corpus, a.output, a.model, a.batch_size, a.threads)


if __name__ == '__main__':
    main()
