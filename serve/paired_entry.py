'''Run a fresh baseline and a supplied agent entry under one configuration.'''
import argparse
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from serve.baseline_entry import config_digest, digest, run
from serve.inference import load_config, write_json
from evaluation.hls import run_process
from evaluation.task_io import load_task
from agent.core.policy import load_policy
from agent.context.skills import Skills
from agent.context.prompts import load_prompts
from evaluation.lifecycle import evaluate as development_evaluation, output_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('problem')
    parser.add_argument('output', help='New directory within project/output')
    parser.add_argument('--config', default='serve/runtime.json')
    parser.add_argument('--agent-entry', default=str(ROOT / 'run.sh'))
    parser.add_argument('--task-manifest', help='Explicit public validation materials for the agent')
    parser.add_argument('--policy', help='Frozen agent policy JSON')
    parser.add_argument('--skills-dir', help='Read-only independently validated rule pack')
    parser.add_argument('--cpu-only', action='store_true')
    args = parser.parse_args()
    return development_evaluation(args, lambda current: _evaluate(current, parser), output=args.output)


def _evaluate(args, parser):
    agent = Path(args.agent_entry).resolve()
    if not agent.is_file() or agent.stat().st_size == 0:
        parser.error('A working run.sh is required; no model request was made')
    output = Path(output_path(args.output)).resolve()
    if not output.is_relative_to(ROOT / 'output'):
        parser.error('Paired output must be within project/output')
    config = load_config(args.config)
    problem_bytes = Path(args.problem).read_bytes()
    task = load_task(args.problem, args.task_manifest)
    policy = load_policy(args.policy)
    skill_pack = Skills(policy['skills_enabled'], args.skills_dir)
    output.mkdir(parents=True, exist_ok=False)
    run_id = uuid.uuid4().hex
    problem = output / 'problem.txt'
    problem.write_bytes(problem_bytes)
    snapshot = output / 'config.json'
    write_json(snapshot, config)
    expected = dict(run_id=run_id, problem_sha256=digest(problem_bytes), config_sha256=config_digest(config))
    expected.update(policy_sha256=config_digest(policy), skills_sha256=skill_pack.sha256)
    prompt_templates = load_prompts()
    expected['prompt_templates_sha256'] = prompt_templates.sha256
    write_json(output / 'prompt_templates.json', prompt_templates.snapshot())
    frozen_files = {}
    manifest_snapshot = None
    if task.manifest is not None:
        task_root = output / 'task_snapshot'
        task_root.mkdir()
        # Material paths are relative to the manifest, so put them in a dedicated
        # directory and choose an unused manifest name there.
        names = {m.name.split('/')[0].casefold() for m in task.materials} | {task.manifest['problem_file'].split('/')[0].casefold(), task.manifest['source_file'].split('/')[0].casefold()}
        manifest_name = '_manifest.json'
        while manifest_name.casefold() in names:
            manifest_name = '_' + manifest_name
        manifest_snapshot = task_root / manifest_name
        write_json(manifest_snapshot, task.manifest)
        for name, data in [(task.manifest['problem_file'], task.problem)] + [(m.name, m.content) for m in task.materials]:
            target = task_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            frozen_files[str(target)] = digest(data)
        frozen_files[str(manifest_snapshot)] = digest(manifest_snapshot.read_bytes())
        expected['task_sha256'] = task.fingerprint
    policy_snapshot = output / 'agent_policy.json'
    write_json(policy_snapshot, policy)
    frozen_files[str(policy_snapshot)] = digest(policy_snapshot.read_bytes())
    skills_snapshot = None
    if policy['skills_enabled']:
        skills_snapshot = output / 'skill_snapshot'
        skills_snapshot.mkdir()
        skill_root = Path(args.skills_dir or ROOT / 'skill/rules').resolve()
        for source in sorted(skill_root.glob('*.json')):
            target = skills_snapshot / source.name
            target.write_bytes(source.read_bytes())
            frozen_files[str(target)] = digest(target.read_bytes())
        if Skills(True, skills_snapshot).sha256 != skill_pack.sha256:
            raise ValueError('Skill pack changed while preparing the paired snapshot')
    pair = dict(**expected, status='running', agent_entry=str(agent), baseline='baseline/result.json', agent='agent/result.json')
    write_json(output / 'pair.json', pair)
    baseline_code, _ = run(problem, output / 'baseline', config, run_id)
    pair['baseline_exit_code'] = baseline_code
    env = os.environ.copy()
    for name in ['LLM_BASE_URL','LLM_MODEL','LLM_MAX_TOKENS']:
        env.pop(name, None)
    env['ZCOMP_RUN_ID'] = run_id
    env['PYTHON'] = sys.executable
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONUTF8'] = '1'
    try:
        command = (['bash', str(agent)] if os.name != 'nt' or agent != ROOT / 'run.sh'
                   else [sys.executable, '-B', '-m', 'agent.interface.entry'])
        command += [str(problem), str(output / 'agent'), '--config', str(snapshot), '--run-id', run_id,
                    '--policy', str(policy_snapshot)]
        if manifest_snapshot:
            command += ['--task-manifest', str(manifest_snapshot)]
        if skills_snapshot:
            command += ['--skills-dir', str(skills_snapshot)]
        if args.cpu_only:
            command += ['--cpu-only']
        execution = run_process(command, ROOT, env, output / 'agent.log', config['hls']['total_timeout_seconds'] + 20)
        pair['agent_exit_code'] = execution['exit_code']
        pair['agent_execution'] = execution
        receipt = json.loads((output / 'agent/result.json').read_text(encoding='utf-8'))
        if any(receipt.get(k) != v for k,v in expected.items()):
            raise ValueError('Agent receipt differs from shared problem, configuration or run ID')
        if config_digest(json.loads(snapshot.read_text(encoding='utf-8'))) != expected['config_sha256'] or digest(problem.read_bytes()) != expected['problem_sha256']:
            raise ValueError('Shared configuration or problem changed during paired run')
        if any(digest(Path(name).read_bytes()) != sha for name, sha in frozen_files.items()):
            raise ValueError('Public validation inputs or frozen agent policy changed during paired run')
        pair['status'] = 'completed' if baseline_code == 0 and execution['exit_code'] == 0 and not execution['timed_out'] else 'completed_with_failures'
        pair['pairing_verified'] = True
        baseline_path = output / 'baseline/result.json'
        baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
        baseline['paired_run'] = True
        write_json(baseline_path, baseline)
    except Exception as error:
        pair.update(status='pairing_failed', pairing_verified=False, error=str(error))
    write_json(output / 'pair.json', pair)
    print(json.dumps(pair))
    return 0 if pair['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
