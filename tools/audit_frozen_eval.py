import hashlib
import json
from pathlib import Path
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

import run_frozen_eval as runner
from run_frozen_eval import ROOT, MANIFEST, VITIS, execute, ok, write

original_env = runner.env_for


def env_for(work):
    env = original_env(work)
    env['LD_LIBRARY_PATH'] = ':'.join(str(VITIS / name) for name in ['lib/lnx64.o', 'lnx64/lib/csim'])
    return env


def parse_check(task):
    work = ROOT / 'results' / task['task_id'] / 'candidate'
    build = work / 'project/solution1/csim/build'
    result = dict(task_id=task['task_id'], status='unknown')
    if not (build / 'csim.mk').is_file():
        return dict(result, reason='Vitis compilation setup unavailable')
    env = env_for(work)
    dry = subprocess.run(['make','-n','-B','-f','csim.mk'],cwd=build,env=env,capture_output=True,text=True,timeout=20)
    commands = []
    for line in dry.stdout.splitlines():
        if not line.startswith(str(VITIS)) or 'clang++' not in line or ' -c ' not in line:
            continue
        line = line.split(' ;')[0]
        args = shlex.split(line)
        if not any(Path(arg).name == task['source'] for arg in args):
            continue
        filtered = []
        skip = False
        for arg in args:
            if skip:
                skip = False
                continue
            if arg in {'-o','-MF','-MT','-MQ'}:
                skip = True
            elif arg not in {'-c','-MMD','-MD',';','\\'}:
                filtered.append(arg)
        commands.append(filtered + ['-fsyntax-only'])
    if len(commands) != 1:
        return dict(result, reason='Could not identify unique Vitis candidate compiler command')
    result['command'] = commands[0]
    result['execution'] = execute(commands[0],build,env,work/'parse.log',120)
    result['status'] = 'passed' if ok(result['execution']) else 'failed'
    if result['execution']['timed_out']:
        result['status'] = 'unknown'
    write(work/'parse_result.json',result)
    return result


def main():
    original = json.loads((ROOT/'summary.json').read_text())
    assert original['status']=='completed' and original['completed']==121
    runner.env_for = env_for
    tasks_by_id = {t['task_id']:t for t in MANIFEST['tasks']}
    for i, record in enumerate(original['results']):
        task_root = ROOT/'results'/record['task_id']
        runtime_logs = list(task_root.glob('*/run.log'))
        if any(any(marker in p.read_text(errors='replace') for marker in ['GLIBCXX', 'CXXABI', 'error while loading shared libraries']) for p in runtime_logs):
            archived = ROOT/'infrastructure_archive'/record['task_id']
            archived.parent.mkdir(parents=True,exist_ok=True)
            task_root.rename(archived)
            fixed = runner.evaluate(tasks_by_id[record['task_id']])
            fixed['infrastructure_rerun'] = str(archived.relative_to(ROOT))
            original['results'][i] = fixed
            print('ENV_RECHECK '+record['task_id']+' '+fixed['status'],flush=True)
    parsed = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(parse_check,MANIFEST['tasks']):
            parsed[result['task_id']] = result
    report = dict(original)
    by_id = {t['task_id']:t for t in MANIFEST['tasks']}
    for result in report['results']:
        task = by_id[result['task_id']]
        result['parse_check'] = parsed[task['task_id']]
        result['checks']['parse'] = parsed[task['task_id']]['status']
        p = ROOT/'inputs'/task['task_id']/task['source']
        result['source_hash_verified'] = hashlib.sha256(p.read_bytes()).hexdigest()==task['source_sha256']
        for name, digest in task['dependencies'].items():
            assert hashlib.sha256((p.parent/name).read_bytes()).hexdigest()==digest
    assert all(r['source_hash_verified'] for r in report['results'])
    report['note'] = 'Vitis 2025.2 development evaluation: parse is bundled clang-16 syntax/semantic check using exact csim compiler flags; compile is csim_design -setup; run checks zero exit and exact deterministic reference testbench output; synthesis requires report and Verilog. No hidden tests, no model calls, no source edits. Not official pass@1/pass@5.'
    report['counts'] = {s:{v:sum(r['checks'][s]==v for r in report['results']) for v in ['passed','failed','unknown','not_run']} for s in ['parse','compile','run','synthesize']}
    report['status_counts'] = {s:sum(r['status']==s for r in report['results']) for s in sorted({r['status'] for r in report['results']})}
    write(ROOT/'audited_summary.json',report)
    print(json.dumps(dict(counts=report['counts'],status_counts=report['status_counts'])),flush=True)


if __name__=='__main__':
    main()
