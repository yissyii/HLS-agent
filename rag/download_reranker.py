"""Download and checksum-pin the optional Qwen3 reranker for offline use."""
import argparse
import hashlib
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--revision')
    args = parser.parse_args()
    os.environ['HF_HUB_DISABLE_XET'] = '1'
    from huggingface_hub import HfApi, snapshot_download

    repository = 'Qwen/Qwen3-Reranker-0.6B'
    output = Path(args.output).resolve()
    manifest_path = output / 'rag-reranker-manifest.json'
    existing = json.loads(manifest_path.read_text('utf-8')) if manifest_path.exists() else None
    revision = args.revision or (existing['revision'] if existing else None)
    info = HfApi().model_info(repository, revision=revision, files_metadata=True)
    print('Reranker revision:', info.sha, flush=True)
    snapshot_download(repo_id=repository, revision=info.sha, local_dir=output, max_workers=2,
                      allow_patterns=['*.json', '*.txt', '*.md', '*.jinja', '*.safetensors',
                                      'LICENSE', 'LICENSE.txt'])
    files = {}
    for path in sorted(output.rglob('*')):
        if not path.is_file() or '.cache' in path.parts or path == manifest_path:
            continue
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        name = path.relative_to(output).as_posix()
        files[name] = {'sha256': digest, 'bytes': path.stat().st_size}
        remote = next((item for item in info.siblings if item.rfilename == name), None)
        if remote and remote.lfs and digest != remote.lfs.sha256:
            raise ValueError('LFS checksum mismatch: ' + name)
    manifest = dict(repository=repository, revision=info.sha, license='Apache-2.0',
                    quantization='none', files=files, purpose='local_reranking_only')
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Verified', len(files), 'files at', output, flush=True)


if __name__ == '__main__':
    main()
