"""Agent CLI and reusable run service. Inputs are explicit, outputs exclusive."""
import argparse
import copy
import json
from pathlib import Path
import sys
import signal
import time
import uuid

from agent.artifacts.writer import Artifacts
from agent.context.skills import Skills
from agent.core.contracts import digest, json_digest
from agent.core.controller import solve
from agent.core.policy import load_policy, validate_policy
from evaluation.task_io import load_task
from evaluation.validator import HLSValidator
from evaluation.lifecycle import evaluate as development_evaluation, output_path
from serve.agent_model import ModelClient
from serve.inference import Failure, ROOT, load_config, validate_config


def run(problem, output, *, config=None, run_id=None, manifest=None, policy=None,
        skills_dir=None, cpu_only=False, initial_source=None, raw_initial=False,
        model=None, validator=None, rag_runtime=None):
    started = time.monotonic()
    artifacts = Artifacts(output)  # Never overwrite previous evidence, even on failure.
    run_id = run_id or uuid.uuid4().hex
    receipt = dict(schema_version=1, branch='agent', run_id=run_id, status='running',
                   problem_sha256=None, config_sha256=None, generation_requests=0,
                   tool_calls=0, validation_status='not_run')
    artifacts.json('result.json', receipt)
    try:
        problem_bytes = Path(problem).read_bytes()
        receipt['problem_sha256'] = digest(problem_bytes)
        runtime = (validate_config(copy.deepcopy(config), frozen=True) if isinstance(config, dict)
                   else load_config(config or ROOT / 'serve/runtime.json', frozen=True, allow_external=True))
        receipt['config_sha256'] = json_digest(runtime)
        artifacts.json('result.json', receipt)
        selected_policy = copy.deepcopy(policy) if isinstance(policy, dict) else load_policy(policy)
        validate_policy(selected_policy)
        task = load_task(problem, manifest)
        if task.problem != problem_bytes:
            raise Failure('input_error', 'Problem changed while loading inputs')
        skill_pack = Skills(selected_policy['skills_enabled'], skills_dir)
        source = Path(initial_source).read_bytes().decode('utf-8') if initial_source is not None else None
        return solve(task, runtime, selected_policy, model or ModelClient(),
                     validator or HLSValidator(cpu_only), artifacts, skill_pack, run_id,
                     initial_source=source, raw_initial=raw_initial, started=started, rag_runtime=rag_runtime)
    except (Failure, OSError, ValueError, KeyError, TypeError) as error:
        category = error.category if isinstance(error, Failure) else 'configuration_or_io_error'
        receipt.update(status='failed', category=category, message=str(error), stop_reason=category,
                       elapsed_seconds=round(time.monotonic() - started, 3), exit_code=1)
        artifacts.event('setup_failed', category=category, message=str(error))
        artifacts.json('result.json', receipt)
        return 1, receipt


def main():
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description='Bounded HLS agent; explicit optional public validation inputs')
    parser.add_argument('problem')
    parser.add_argument('output')
    parser.add_argument('--config', default=str(ROOT / 'serve/runtime.json'))
    parser.add_argument('--run-id')
    parser.add_argument('--task-manifest')
    parser.add_argument('--policy')
    parser.add_argument('--skills-dir')
    parser.add_argument('--rag-runtime', help='Local RAG paths JSON; read only when RAG is enabled in policy')
    parser.add_argument('--initial-source', help='Path to a pre-generated first draft (raw model output); skips generation')
    parser.add_argument('--cpu-only', action='store_true', help='Hide GPUs from HLS only, not from the model server')
    args = parser.parse_args()
    return development_evaluation(args, _evaluate, output=args.output)


def _evaluate(args):
    try:
        code, receipt = run(args.problem, output_path(args.output), config=args.config, run_id=args.run_id,
                            manifest=args.task_manifest, policy=args.policy,
                            skills_dir=args.skills_dir, cpu_only=args.cpu_only, rag_runtime=args.rag_runtime,
                            initial_source=args.initial_source)
        print(json.dumps(receipt, ensure_ascii=False))
        return code
    except (Failure, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
