import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT.parent / 'hls-overlap-audit/hls-eval/hls_eval_data'
OUT = ROOT / 'output/frozen_eval_121_20260915'


def read(p):
    return json.loads(p.read_text(encoding='utf-8'))


def main():
    sources = {r['task_id']: r['source_path'] for r in read(ROOT / 'output/generation_raw_continue_20260915/summary.json')['tasks']}
    for r in read(ROOT / 'output/network_retry_20260915T103212Z/summary.json')['results']:
        if r['status'] == 'generated':
            assert r['task_id'] not in sources
            sources[r['task_id']] = r['source_path']
    bfs = read(ROOT / 'output/bfs_network_retry_20260915T104537Z/summary.json')
    assert bfs['status'] == 'completed' and bfs['task_id'] not in sources
    sources[bfs['task_id']] = bfs['source_path']
    assert len(sources) == 121 and 'chstone/df_countLeadingZeros64' not in sources
    OUT.mkdir(exist_ok=False)
    records = []
    for task_id, source_path in sorted(sources.items()):
        task = DATA / task_id
        source = Path(source_path)
        tb = list(task.glob('*_tb.cpp'))
        assert len(tb) == 1
        config = tomllib.loads((task / 'hls_eval_config.toml').read_text(encoding='utf-8'))
        assert set(config) <= {'tags', 'tb_data'}, (task_id, config)
        dest = OUT / 'inputs' / task_id
        dest.mkdir(parents=True)
        deps = [p for p in task.iterdir() if p.is_file() and p.suffix in {'.h', '.hpp', '.hh'}]
        deps += [tb[0]] + [task / n for n in config.get('tb_data', [])]
        for p in deps:
            shutil.copyfile(p, dest / p.name)
        shutil.copyfile(source, dest / source.name)
        ref = OUT / 'references' / task_id
        ref.mkdir(parents=True)
        shutil.copyfile(task / source.name, ref / source.name)
        records.append(dict(task_id=task_id, source=source.name, testbench=tb[0].name,
                            top=(task / 'top.txt').read_text().strip(), tb_data=config.get('tb_data', []),
                            source_origin=source_path, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                            dependencies={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in deps}))
    manifest = dict(task_count=121, dataset_revision='9eb7addc7103b44d25eee61fc356e3f50cfc28e9',
                    part='xczu3eg-sbva484-1-e', clock_ns=5, cxx_standard='c++14',
                    compile_timeout=180, run_timeout=120, synth_timeout=300,
                    source_mutations=False, model_requests=0, tasks=records)
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    shutil.copyfile(ROOT / 'tools/run_frozen_eval.py', OUT / 'run_frozen_eval.py')
    archive = OUT.with_suffix('.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(OUT, arcname=OUT.name)
    print(json.dumps(dict(bundle=str(OUT), archive=str(archive), tasks=len(records), bytes=archive.stat().st_size)))


if __name__ == '__main__':
    main()
