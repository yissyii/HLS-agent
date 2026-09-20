"""Explicit preparation: download original Qwen weights and pin all local files."""
import argparse
import hashlib
import json
from pathlib import Path
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


def ranged_weights(output, info):
    """Resume small HTTP ranges when the large-file transfer stalls; verify upstream SHA."""
    import requests
    entry = next(s for s in info.siblings if s.rfilename == 'model.safetensors')
    size, expected = entry.lfs.size, entry.lfs.sha256
    destination = output / entry.rfilename
    if destination.exists():
        with destination.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() == expected:
                return
    parts = output / '.cache' / ('ranges-' + info.sha)
    parts.mkdir(parents=True, exist_ok=True)
    block_size = 8 * 1024 * 1024
    spans = [(i, min(i + block_size, size) - 1) for i in range(0, size, block_size)]

    def fetch(span):
        start, end = span
        part = parts / str(start)
        if part.exists() and part.stat().st_size == end - start + 1:
            return
        # Separate cache keys prevent intermediaries serving a different cached range.
        url = f'https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/resolve/{info.sha}/model.safetensors?download=true&part={start}'
        for attempt in range(4):
            try:
                response = requests.get(url, headers={'Range': f'bytes={start}-{end}'}, timeout=(15, 45))
                response.raise_for_status()
                if response.status_code != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{size}' or len(response.content) != end - start + 1:
                    raise ValueError('Unexpected HTTP range response')
                temporary = part.with_suffix('.tmp')
                temporary.write_bytes(response.content)
                temporary.replace(part)
                return
            except (requests.RequestException, ValueError):
                if attempt == 3:
                    raise
                time.sleep(attempt + 1)
    with ThreadPoolExecutor(max_workers=4) as executor:
        for done, future in enumerate(as_completed([executor.submit(fetch, span) for span in spans]), 1):
            future.result()
            print(f'Weight ranges {done}/{len(spans)}', flush=True)
    temporary = destination.with_suffix('.assembling')
    digest = hashlib.sha256()
    with temporary.open('wb') as stream:
        for start, _ in spans:
            data = (parts / str(start)).read_bytes()
            digest.update(data)
            stream.write(data)
    if digest.hexdigest() != expected:
        raise ValueError('Assembled weights differ from upstream LFS checksum')
    temporary.replace(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--revision')
    parser.add_argument('--range-download', action='store_true')
    args = parser.parse_args()
    os.environ['HF_HUB_DISABLE_XET'] = '1'
    from huggingface_hub import HfApi, snapshot_download

    output = Path(args.output).resolve()
    manifest_path = output / 'rag-model-manifest.json'
    existing = json.loads(manifest_path.read_text('utf-8')) if manifest_path.exists() else None
    revision = args.revision or (existing['revision'] if existing else None)
    api = HfApi()
    info = api.model_info('Qwen/Qwen3-Embedding-0.6B', revision=revision, files_metadata=True)
    print('Model revision:', info.sha, flush=True)
    if args.range_download:
        ranged_weights(output, info)
    snapshot_download(repo_id='Qwen/Qwen3-Embedding-0.6B', revision=info.sha,
                      local_dir=output, max_workers=2,
                      allow_patterns=['*.json', '*.txt', '*.md', 'LICENSE', 'LICENSE.txt'] + ([] if args.range_download else ['*.safetensors']))
    files = {}
    for path in sorted(output.rglob('*')):
        if not path.is_file() or '.cache' in path.parts or path == manifest_path:
            continue
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(block)
        name = path.relative_to(output).as_posix()
        files[name] = {'sha256': h.hexdigest(), 'bytes': path.stat().st_size}
        remote = next((s for s in info.siblings if s.rfilename == name), None)
        if remote and remote.lfs and h.hexdigest() != remote.lfs.sha256:
            raise ValueError('LFS checksum mismatch: ' + name)
    manifest = dict(repository='Qwen/Qwen3-Embedding-0.6B', revision=info.sha,
                    license='Apache-2.0', quantization='none', dimensions=1024,
                    files=files, purpose='local_retrieval_only')
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Verified', len(files), 'files at', output, flush=True)


if __name__ == '__main__':
    main()
