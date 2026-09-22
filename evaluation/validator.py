"""Per-candidate Vitis workspaces and explicit feedback visibility."""
from pathlib import Path
import time

from agent.core.contracts import ValidationResult, digest, json_digest
from evaluation.hls import environment, validate_stage
from serve.inference import Failure
from evaluation.functional import format_functional


class HLSValidator:
    def __init__(self, cpu_only=False):
        self.cpu_only = cpu_only

    def preflight(self, task, runtime, directory):
        if task.stages:
            work = Path(directory) / 'preflight'
            work.mkdir()
            environment(work, runtime['hls'], self.cpu_only)

    def check(self, candidate, task, runtime, stage, deadline):
        if stage not in task.stages:
            raise Failure('input_error', 'Stage has no authorized validation materials')
        return self._check(candidate, task, runtime, stage, deadline, 'work', stage)

    def selftest(self, candidate, task, runtime, deadline):
        if 'csim' not in task.stages:
            raise Failure('selftest_unavailable', 'Generated self-test task has no executable testbench')
        return self._check(candidate, task, runtime, 'csim', deadline, 'selftest_work', 'selftest')

    def _check(self, candidate, task, runtime, stage, deadline, work_name, result_stage):
        work = candidate.path.parent / work_name
        inputs = work / 'input'
        inputs.mkdir(parents=True, exist_ok=True)
        # Rewrite only this candidate's snapshot, never the task's originals.
        for material in task.materials:
            path = inputs / material.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(material.content)
        source = inputs / task.manifest['source_file']
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(candidate.source.encode('utf-8'))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Failure('total_timeout', 'No time left for validation')
        outcome = validate_stage(stage, work, task.manifest, runtime['hls'], self.cpu_only, remaining)
        for key in ('log', 'report'):
            if key in outcome:
                outcome[key] = (work / outcome[key]).relative_to(candidate.path.parents[2]).as_posix()
        if digest(source.read_bytes()) != candidate.sha256:
            raise Failure('evidence_mismatch', 'Source changed during validation')
        for material in task.materials:
            if (inputs / material.name).read_bytes() != material.content:
                raise Failure('evidence_mismatch', 'Validation input changed during execution')
        checks = {}
        if result_stage == 'selftest':
            checks = {}
        elif stage == 'synthesis':
            checks['synthesize'] = outcome['status']
        elif outcome['status'] == 'passed':
            checks.update(parse='passed', compile='passed', run='passed')
        elif outcome.get('category') == 'functional_or_runtime_error':
            checks.update(parse='passed', compile='passed', run='failed')
        elif outcome.get('category') == 'compile_error':
            checks.update(parse='unknown', compile='failed', run='not_run')
        else:
            checks.update(parse='unknown', compile='unknown', run='not_run')
        category = outcome.get('category', 'passed')
        policy = task.manifest['feedback_policy']
        if policy == 'public_diagnostics':
            feedback = outcome.get('diagnostic_tail', category)
        elif policy == 'compiler_diagnostics':
            feedback = outcome.get('compiler_text') or category
        elif policy == 'functional_diagnostics':
            # This opt-in policy authorizes structured functional facts, not a raw log tail.
            if category in {'compile_error', 'synthesis_error', 'compile_or_csim_error'}:
                feedback = outcome.get('compiler_text') or category
            elif category == 'functional_or_runtime_error':
                feedback = format_functional(outcome.get('functional_diagnostics')) or category
            else:
                feedback = category
        else:
            feedback = f'{stage}: {category}. Detailed diagnostics are not released by this task.'
        return ValidationResult(candidate.sha256, task.fingerprint, json_digest(runtime['hls']),
                                result_stage, outcome, checks, feedback)
