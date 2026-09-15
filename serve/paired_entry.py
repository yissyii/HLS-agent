'''Run a fresh baseline and a supplied agent entry under one configuration.'''
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from serve.baseline_entry import config_digest, digest, run
from serve.inference import load_config, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('problem')
    parser.add_argument('output', help='New directory within project/output')
    parser.add_argument('--config', default='serve/runtime.json')
    parser.add_argument('--agent-entry', default=str(ROOT / 'run.sh'))
    args = parser.parse_args()
    agent = Path(args.agent_entry).resolve()
    if not agent.is_file() or agent.stat().st_size == 0:
        parser.error('A working run.sh is required; no model request was made')
    output = Path(args.output).resolve()
    if not output.is_relative_to(ROOT / 'output'):
        parser.error('Paired output must be within project/output')
    config = load_config(args.config)
    problem_bytes = Path(args.problem).read_bytes()
    output.mkdir(parents=True, exist_ok=False)
    run_id = uuid.uuid4().hex
    problem = output / 'problem.txt'
    problem.write_bytes(problem_bytes)
    snapshot = output / 'config.json'
    write_json(snapshot, config)
    expected = dict(run_id=run_id, problem_sha256=digest(problem_bytes), config_sha256=config_digest(config))
    pair = dict(**expected, status='running', agent_entry=str(agent), baseline='baseline/result.json', agent='agent/result.json')
    write_json(output / 'pair.json', pair)
    baseline_code, _ = run(problem, output / 'baseline', config, run_id)
    pair['baseline_exit_code'] = baseline_code
    env = os.environ.copy()
    for name in ['LLM_BASE_URL','LLM_MODEL','LLM_MAX_TOKENS']:
        env.pop(name, None)
    env['ZCOMP_RUN_ID'] = run_id
    try:
        with (output / 'agent.log').open('wb') as log:
            process = subprocess.run(['bash', str(agent), str(problem), str(output / 'agent'), '--config', str(snapshot), '--run-id', run_id],
                                     cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        pair['agent_exit_code'] = process.returncode
        receipt = json.loads((output / 'agent/result.json').read_text(encoding='utf-8'))
        if any(receipt.get(k) != v for k,v in expected.items()):
            raise ValueError('Agent receipt differs from shared problem, configuration or run ID')
        if config_digest(json.loads(snapshot.read_text())) != expected['config_sha256'] or digest(problem.read_bytes()) != expected['problem_sha256']:
            raise ValueError('Shared configuration or problem changed during paired run')
        pair['status'] = 'completed' if baseline_code == 0 and process.returncode == 0 else 'completed_with_failures'
        pair['pairing_verified'] = True
        baseline_path = output / 'baseline/result.json'
        baseline = json.loads(baseline_path.read_text())
        baseline['paired_run'] = True
        write_json(baseline_path, baseline)
    except Exception as error:
        pair.update(status='pairing_failed', pairing_verified=False, error=str(error))
    write_json(output / 'pair.json', pair)
    print(json.dumps(pair))
    return 0 if pair['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
