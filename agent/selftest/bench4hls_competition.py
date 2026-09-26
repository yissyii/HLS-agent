"""Bench4HLS competition simulation: public self-test repair, then sealed official judge."""
from dataclasses import asdict
from datetime import datetime, timezone
import copy
import json
from pathlib import Path
import re
import time
import uuid

from agent.artifacts.writer import Artifacts
from agent.context.skills import Skills
from agent.core.contracts import Candidate, Material, PromptBundle, TaskSpec, digest, json_digest
from agent.core.controller import solve
from agent.core.policy import load_policy, validate_policy
from evaluation.task_io import load_task
from evaluation.validator import HLSValidator
from serve.agent_model import ModelClient
from serve.inference import Failure, ROOT, load_config, validate_config
from .spec import keys, parse_json, require
from .bench4hls_semantics import plan_testbench


VERSION = 'bench4hls-selftest-competition-0.2.0'
PROMPT = Path(__file__).parent / 'prompts/bench4hls_testbench.md'


class DevelopmentTask(TaskSpec):
    """A public task whose only validation stage is the frozen self-test csim."""
    @property
    def stages(self):
        return ('csim',)


def make_prompt(problem, top_function, plan=None):
    system = PROMPT.read_text(encoding='utf-8')
    values = dict(public_problem=problem.decode('utf-8'), public_top_function=top_function)
    if plan is not None:
        system = (Path(__file__).parent/'prompts/bench4hls_planned_testbench.md').read_text(encoding='utf-8')
        values['validation_plan'] = plan
    payload = json.dumps(values, ensure_ascii=False)
    return PromptBundle(payload,
        [dict(kind='public_problem', sha256=digest(problem)),
         dict(kind='public_top_function', value=top_function)],
        dict(version=VERSION, official_testbench_visible=False, candidate_visible=False),
        system=system)


def normalize_testbench(raw, top_function):
    keys(raw, {'schema_version', 'decision', 'testbench_cpp', 'reasons'}, 'competition self-test')
    require(type(raw['schema_version']) is int and raw['schema_version'] == 1, 'Self-test schema must be 1')
    require(isinstance(raw['reasons'], list) and all(isinstance(v, str) and v.strip() for v in raw['reasons']),
            'Self-test reasons must be nonempty strings')
    if raw['decision'] == 'abstain':
        require(raw['testbench_cpp'] is None and raw['reasons'], 'Invalid self-test abstention')
        return None
    require(raw['decision'] == 'ready' and not raw['reasons'], 'Invalid self-test decision')
    source = raw['testbench_cpp']
    require(isinstance(source, str) and 0 < len(source.encode('utf-8')) <= 256_000, 'Invalid self-test source size')
    require(re.search(r'\bint\s+main\s*\(', source) is not None, 'Self-test needs main()')
    require(re.search(r'\b' + re.escape(top_function) + r'\s*\(', source) is not None,
            'Self-test does not call the public top function')
    require('ZCOMP_FUNCTIONAL ' in source, 'Self-test needs structured mismatch diagnostics')
    require('.to_ullong(' not in source, 'Self-test uses an API unavailable in Vitis 2026.1')
    forbidden = (r'#\s*include\s*"[^"]+\.(?:c|cc|cpp|cxx)"',
                 r'\b(?:ifstream|ofstream|fstream|filesystem|random_device)\b',
                 r'\b(?:fopen|freopen|system|popen|getenv|dlopen)\s*\(',
                 r'(?:[A-Za-z]:[\\/]|/(?:home|root|mnt|tmp|etc)/)')
    require(not any(re.search(pattern, source, re.I) for pattern in forbidden),
            'Self-test uses forbidden external input or execution')
    return source


def parse_testbench_response(text):
    try:
        return parse_json(text)
    except ValueError as json_error:
        ready = re.fullmatch(r'\s*ZCOMP_SELFTEST_READY\s*```(?:cpp|c\+\+|cxx)\s*\n(.*?)\n```\s*',
                             text, re.S | re.I)
        if ready:
            return dict(schema_version=1, decision='ready', testbench_cpp=ready.group(1), reasons=[])
        abstain = re.fullmatch(r'\s*ZCOMP_SELFTEST_ABSTAIN\s*\n(.+?)\s*', text, re.S)
        if abstain:
            return dict(schema_version=1, decision='abstain', testbench_cpp=None,
                        reasons=[abstain.group(1).strip()])
        raise json_error


def generate_testbench(problem, top_function, output, *, model, runtime, deadline, response=None,
                       mode='legacy'):
    if mode in ('contract', 'bindings') and response is None:
        from .bench4hls_contract_generation import generate_contract, generate_bindings
        generator = generate_bindings if mode == 'bindings' else generate_contract
        return generator(problem, top_function, output, model=model, runtime=runtime, deadline=deadline)
    directory = Path(output); directory.mkdir(parents=True, exist_ok=False)
    result = dict(version=VERSION, status='failed', model_requests=0,
                  generation_mode=mode,
                  official_testbench_visible=False, candidate_visible=False,
                  semantic_correctness='unverified', automatic_acceptance_allowed=False)
    try:
        require(mode in {'legacy', 'semantic'}, 'Unknown self-test generation mode')
        plan = None
        if mode == 'semantic' and response is None:
            planning = plan_testbench(problem, top_function, directory/'semantic_plan',
                                      model=model, runtime=runtime, deadline=deadline)
            result['semantic_planning'] = planning
            result['model_requests'] += planning['model_requests']
            if planning['status'] != 'planned':
                if planning['status'] == 'abstained':
                    result.update(status='abstained', reasons=planning['plan']['blockers'])
                else:
                    result.update(category=planning.get('category', 'semantic_plan_failed'),
                                  message=planning.get('message', 'Semantic planning failed'))
                return _save_generation(directory, result)
            plan = planning['plan']
            result.update(coverage_scope=plan['decision'], excluded_behaviors=plan['excluded'],
                          planned_check_ids=[check['id'] for check in plan['checks']],
                          plan_sha256=json_digest(plan))
        elif response is not None:
            result['coverage_scope'] = 'recorded_unverified'
        prompt = make_prompt(problem, top_function, plan)
        (directory/'prompt.txt').write_text(prompt.text, encoding='utf-8')
        (directory/'system_prompt.txt').write_text(prompt.system, encoding='utf-8')
        if response is None:
            result['model_requests'] += 1
            metadata = model.generate(prompt, runtime, directory, deadline)
            result['metadata'] = metadata
            if metadata.get('status') != 'passed':
                raise Failure(metadata.get('category', 'generation_error'), 'Self-test model request failed')
            raw = parse_testbench_response((directory/'response.txt').read_text(encoding='utf-8'))
        else:
            raw = copy.deepcopy(response)
        (directory/'response.json').write_text(json.dumps(raw, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        source = normalize_testbench(raw, top_function)
        if source is None:
            result.update(status='abstained', reasons=raw['reasons'])
        else:
            (directory/'selftest.cpp').write_text(source, encoding='utf-8')
            result.update(status='generated_unreviewed', source_sha256=digest(source.encode('utf-8')))
    except (Failure, OSError, ValueError, TypeError, KeyError) as error:
        result.update(category=getattr(error, 'category', 'selftest_generation_error'), message=str(error))
    return _save_generation(directory, result)


def _save_generation(directory, result):
    result['receipt_id'] = json_digest(result)
    (directory/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return result


def development_task(problem, public_manifest, testbench):
    manifest = dict(id='selftest_' + public_manifest['id'], top_function=public_manifest['top_function'],
                    problem_file='problem.txt', source_file=public_manifest.get('source_file', 'kernel.cpp'),
                    testbench_files=['selftest.cpp'], design_files=[], support_files=[], include_dirs=['.'],
                    model_visible=[], cxx_standard=public_manifest.get('cxx_standard', 'c++14'),
                    feedback_policy='functional_diagnostics')
    return DevelopmentTask(problem, manifest, [Material('selftest.cpp', testbench.encode('utf-8'), False)])


def _public_manifest(path, problem):
    raw = json.loads(Path(path).read_text(encoding='utf-8'))
    allowed = {'id', 'top_function', 'problem_file', 'source_file', 'cxx_standard'}
    public = {k: raw[k] for k in allowed if k in raw}
    require(all(isinstance(public.get(k), str) and public[k] for k in ('id', 'top_function', 'problem_file')),
            'Invalid public task metadata')
    require((Path(path).parent/public['problem_file']).read_bytes() == problem, 'Manifest problem changed')
    return public


def _final_judge(problem_path, manifest_path, submission, output, runtime, validator):
    """Load official materials only after submission is frozen; never call a model."""
    artifacts = Artifacts(output)
    task = load_task(problem_path, manifest_path)
    submission_bytes = Path(submission).read_bytes()
    source = submission_bytes.decode('utf-8')
    candidate_dir = artifacts.root/'candidates/000'; candidate_dir.mkdir(parents=True)
    candidate_path = candidate_dir/'candidate.cpp'; candidate_path.write_text(source, encoding='utf-8')
    candidate = Candidate(0, source, candidate_path, None)
    before = candidate.sha256
    result = dict(status='running', task_sha256=task.fingerprint, source_sha256=before,
                  official_materials_loaded=True, feedback_to_model=False, stages=[], checks={})
    artifacts.json('result.json', result)
    try:
        validator.preflight(task, runtime, artifacts.root)
        for stage in task.stages:
            outcome = validator.check(candidate, task, runtime, stage,
                                      time.monotonic()+runtime['hls']['total_timeout_seconds'])
            result['stages'].append(asdict(outcome)); result['checks'].update(outcome.checks)
            if outcome.outcome.get('status') != 'passed': break
        require(candidate.sha256 == before and Path(submission).read_bytes() == submission_bytes,
                'Frozen submission changed during official judging')
        passed = bool(task.stages) and all(result['checks'].get('run' if s=='csim' else 'synthesize')=='passed'
                                           for s in task.stages)
        result['status'] = 'passed' if passed else 'failed'
    except (Failure, OSError, ValueError, TypeError, KeyError) as error:
        result.update(status='inconclusive', category=getattr(error, 'category', 'official_judge_error'), message=str(error))
    artifacts.json('result.json', result)
    return result


def run_task(task_directory, output, *, config=None, policy=None, cpu_only=False,
             model=None, selftest_validator=None, official_validator=None,
             recorded_selftest=None, run_id=None, selftest_mode='legacy'):
    """Run one isolated contest attempt. Output must not exist."""
    started = time.monotonic(); task_directory = Path(task_directory).resolve()
    artifacts = Artifacts(output); run_id = run_id or uuid.uuid4().hex
    problem_path = task_directory/'problem.txt'; manifest_path = task_directory/'task.json'
    problem = problem_path.read_bytes(); public = _public_manifest(manifest_path, problem)
    runtime = (validate_config(copy.deepcopy(config), frozen=True) if isinstance(config, dict)
               else load_config(config or ROOT/'serve/runtime.json', frozen=True, allow_external=True))
    selected_policy = copy.deepcopy(policy) if isinstance(policy, dict) else load_policy(policy)
    validate_policy(selected_policy); model = model or ModelClient()
    selftest_validator = selftest_validator or HLSValidator(cpu_only)
    official_validator = official_validator or HLSValidator(cpu_only)
    summary = dict(schema_version=1, version=VERSION, run_id=run_id, task_id=public['id'], status='running',
                   started_at=datetime.now(timezone.utc).isoformat(), problem_sha256=digest(problem),
                   public_manifest=public, config_sha256=json_digest(runtime), policy_sha256=json_digest(selected_policy),
                   isolation=dict(official_materials_loaded_after_submission=True,
                                  official_result_feedback_to_model=False), model_requests=0)
    summary['validation_backend'] = getattr(selftest_validator, 'provenance', {'backend':'local'})
    artifacts.json('result.json', summary)
    deadline = started + runtime['hls']['total_timeout_seconds']
    generation = generate_testbench(problem, public['top_function'], artifacts.root/'selftest_generation',
                                    model=model, runtime=runtime, deadline=deadline, response=recorded_selftest,
                                    mode=selftest_mode)
    summary['selftest_generation'] = generation; summary['model_requests'] += generation['model_requests']
    feedback_allowed = (generation['status'] == 'generated_unreviewed'
                        and selftest_mode not in ('contract', 'bindings')
                        and generation.get('repair_feedback_allowed', True) is True)
    summary['selftest_feedback'] = dict(allowed=feedback_allowed,
        reason='experimental_contract_requires_calibration' if selftest_mode in ('contract', 'bindings')
        else 'legacy_policy' if feedback_allowed else 'selftest_unavailable_or_withheld')
    if feedback_allowed:
        dev_task = development_task(problem, public,
            (artifacts.root/'selftest_generation/selftest.cpp').read_text(encoding='utf-8'))
        dev_scope = 'frozen_generated_selftest'
    else:
        dev_task = TaskSpec(problem)
        dev_scope = ('generation_only_selftest_withheld' if generation['status'] == 'generated_unreviewed'
                     else 'generation_only_selftest_unavailable')
    remaining = max(1, deadline-time.monotonic())
    dev_runtime = copy.deepcopy(runtime); dev_runtime['hls']['total_timeout_seconds'] = remaining
    development = Artifacts(artifacts.root/'development')
    _, dev_result = solve(dev_task, dev_runtime, selected_policy, model, selftest_validator,
                          development, Skills(False, None), run_id, started=time.monotonic())
    summary['development'] = dev_result; summary['development_scope'] = dev_scope
    summary['model_requests'] += dev_result.get('generation_requests', 0)
    candidate_path = development.root/'candidate.cpp'
    if not candidate_path.is_file():
        summary.update(status='failed', stop_reason='no_submission', elapsed_seconds=round(time.monotonic()-started,3))
        artifacts.json('result.json', summary); return summary
    submission = artifacts.bytes('submission.cpp', candidate_path.read_bytes())
    summary['submission'] = dict(path='submission.cpp', sha256=digest(submission.read_bytes()),
                                 selected_candidate=dev_result.get('selected_candidate'),
                                 freeze_reason=dev_result.get('stop_reason'))
    # This line is the contest boundary: official testbench/dependencies are loaded only below.
    final = _final_judge(problem_path, manifest_path, submission, artifacts.root/'official_judge',
                         runtime, official_validator)
    summary['official_judge'] = final
    summary.update(status='passed' if final['status']=='passed' else 'completed',
                   official_passed=final['status']=='passed',
                   elapsed_seconds=round(time.monotonic()-started,3),
                   finished_at=datetime.now(timezone.utc).isoformat())
    artifacts.json('result.json', summary)
    return summary
