'''Preserve historical generation and evaluation artifacts without relabeling them.'''
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
NAMES = ['generation_only_20260914_2344', 'generation_resume_20260915',
         'generation_raw_20260915', 'generation_raw_continue_20260915',
         'network_retry_20260915T103212Z', 'bfs_network_retry_20260915T104537Z']


def main():
    destination = ROOT / 'output' / ('baseline_evidence_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    destination.mkdir(exist_ok=False)
    archive = destination / 'historical_artifacts.tar.gz'
    files = []
    for name in NAMES:
        directory = ROOT / 'output' / name
        assert directory.is_dir(), directory
        files.extend(p for p in directory.rglob('*') if p.is_file())
    for name in ['frozen_eval_121_20260915.tar.gz', 'frozen_eval_121_20260915_results.tar.gz']:
        p = ROOT / 'output' / name
        assert p.is_file(), p
        files.append(p)
    records = []
    with tarfile.open(archive, 'w:gz') as tar:
        for p in sorted(files):
            relative = p.relative_to(ROOT).as_posix()
            payload = p.read_bytes()
            records.append(dict(path=relative, bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest()))
            tar.add(p, arcname=relative)
    with tarfile.open(archive, 'r:gz') as tar:
        for item in records:
            assert hashlib.sha256(tar.extractfile(item['path']).read()).hexdigest() == item['sha256']
    manifest = dict(classification='historical_development_results_not_official_gain_baseline',
                    frozen_test_set=True, model_or_prompt_changes_from_test_results=False,
                    historical_prompt_intervention_disclosed=True,
                    notes=['Historical runs include prompt wrapping, supplied headers, retries and interrupted sessions.',
                           'Altered-prompt diagnostic results are not baseline results.',
                           'No paired agent run exists for these artifacts; no official pass@1/pass@5 claim.',
                           'No source artifacts were modified or removed by preservation.'],
                    generated_samples_saved=121, total_tasks=122,
                    evaluation_counts=dict(parse=107,compile=106,run=59,synthesize=49),
                    archive=archive.name, archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), files=records)
    (destination/'index.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(dict(directory=str(destination),files=len(records),archive_bytes=archive.stat().st_size)))


if __name__=='__main__':
    main()
