"""Legacy manifest CLI backed by the shared agent controller."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

from agent.core.policy import load_policy
from agent.interface.entry import run
from evaluation.task_io import load_manifest, relative_name
from evaluation.lifecycle import evaluate as development_evaluation, output_path
from serve.code import extract_code
from serve.inference import Failure, ROOT, load_config, project_path, write_json


def evaluate(args):
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:8]
    work = output_path(ROOT / 'output/runs' / run_id)
    # Keep the old entry's local-configuration preference and project path rules.
    try:
        runtime = load_config(args.config)
        manifest_path = project_path(args.manifest)
        manifest, _ = load_manifest(manifest_path)
        if not manifest['testbench_files']:
            raise Failure('input_error', 'Legacy run_eval requires a self-checking testbench')
        problem = manifest_path.parent / manifest['problem_file']
        source = project_path(args.source) if args.source else None
        policy = load_policy()
        policy['max_repairs'] = args.repair_attempts
        _, result = run(problem, work, config=runtime, run_id=run_id, manifest=manifest_path,
                        policy=policy, cpu_only=args.cpu_only, initial_source=source,
                        raw_initial=True)
    except (Failure, OSError, ValueError, KeyError, TypeError) as error:
        work.mkdir(parents=True, exist_ok=True)
        result = dict(schema_version=1, run_id=run_id, status='failed',
                      category=getattr(error, 'category', 'configuration_or_io_error'),
                      message=str(error), api_requests_recorded=0)
    result['mode'] = (('repair_existing' if args.source else 'repair_and_validate') if args.repair_attempts
                      else ('validate_existing' if args.source else 'baseline_and_validate'))
    result['cpu_only_requested'] = args.cpu_only
    # The legacy evaluator's exit status still means validation success, whereas
    # the new agent entry's exit status means candidate delivery.
    passed = result.get('status') != 'failed' and all(
        result.get('checks', {}).get(k) == 'passed' for k in ('run', 'synthesize'))
    result['status'] = 'passed' if passed else 'failed'
    result['exit_code'] = 0 if passed else 1
    write_json(work / 'result.json', result)
    print(json.dumps({'status': result['status'], 'category': result.get('category'),
                      'result': str((work / 'result.json').relative_to(ROOT))}, ensure_ascii=False))
    return result['exit_code']


def main():
    parser = argparse.ArgumentParser(description='Legacy single-task HLS evaluation using the shared agent controller')
    parser.add_argument('manifest')
    parser.add_argument('--source')
    parser.add_argument('--config')
    parser.add_argument('--cpu-only', action='store_true')
    parser.add_argument('--repair-attempts', type=int, default=0, choices=range(6))
    args = parser.parse_args()
    return development_evaluation(args, evaluate)


if __name__ == '__main__':
    raise SystemExit(main())
