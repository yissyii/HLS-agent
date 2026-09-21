"""One bounded controller used by the new entry and legacy evaluation."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
import time

from agent.candidates.manager import Candidates
from agent.context.builder import build
from agent.context.prompts import load_prompts
from agent.context.retrieval import Retrieval
from agent.core.contracts import Budget, ValidationResult, digest, empty_checks, json_digest
from agent.core.policy import decide
from agent.feedback.diagnostics import classify
from serve.code import extract_code
from serve.inference import Failure


def solve(task, runtime, policy, model, validator, artifacts, skills, run_id,
          *, initial_source=None, raw_initial=False, started=None, rag_runtime=None,
          initial_validation=None):
    started = time.monotonic() if started is None else started
    budget = Budget(started, started + runtime['hls']['total_timeout_seconds'] - policy['cleanup_reserve_seconds'])
    candidates = Candidates(task.fingerprint, json_digest(runtime['hls']))
    templates = load_prompts()  # One immutable prompt snapshot for all attempts in this run.
    rag = Retrieval(policy, rag_runtime)
    summary = dict(schema_version=1, branch='agent', run_id=run_id, status='running',
                   problem_sha256=digest(task.problem), config_sha256=json_digest(runtime),
                   policy_sha256=json_digest(policy), task_sha256=task.fingerprint,
                   skills_sha256=skills.sha256, model=runtime['model'], agent_used=True,
                   prompt_templates_version=templates.version, prompt_templates_sha256=templates.sha256,
                   raw_initial=raw_initial,
                   rag_enabled=rag.enabled, rag_config_sha256=rag.sha256, rag_history=[],
                   skills_used=False, started_at=datetime.now(timezone.utc).isoformat(),
                   checks=empty_checks(), generation_requests=0, tool_calls=0,
                   repair_attempts_allowed=policy['max_repairs'], repair_attempts_used=0,
                   stages={k: {'status': 'not_run'} for k in ('generation', 'csim', 'synthesis')},
                   validation_scope=('public_tests_and_synthesis' if 'csim' in task.stages else
                                     'synthesis_only' if task.stages else 'none'),
                   check_note='Parse and compile are observed together through Vitis csim; no independent official parse score.',
                   requests=[], validation_history=[], paired_run=False)
    artifacts.bytes('problem.txt', task.problem)
    artifacts.json('config.json', runtime)
    artifacts.json('policy.json', policy)
    artifacts.json('prompt_templates.json', templates.snapshot())
    artifacts.json('rag.json', rag.snapshot)
    artifacts.json('task.json', task.snapshot())
    for material in task.materials:
        artifacts.bytes('materials/' + material.name, material.content)
    artifacts.json('result.json', summary)
    previous = None
    diagnostic = None
    last_fingerprint = None
    last_rank = -1
    stagnant = 0
    stop = 'not_started'
    fatal = False
    try:
        if budget.remaining() <= 0:
            raise Failure('total_timeout', 'Budget exhausted during input preparation')
        validator.preflight(task, runtime, artifacts.root)
        for number in range(policy['max_repairs'] + 1):
            if budget.remaining() <= 0:
                raise Failure('total_timeout', 'Single-task budget exhausted')
            directory = artifacts.attempt(number)
            extraction_details = {}
            top_function = task.manifest['top_function'] if task.manifest else None
            if number == 0 and initial_source is not None:
                source, extraction = extract_code(initial_source, top_function=top_function, details=extraction_details)
                summary['stages']['generation'] = {'status': 'skipped', 'requests': 0}
            else:
                selected_skills = skills.select(diagnostic, policy['max_skills'])
                retrieval = None
                if diagnostic is not None and rag.enabled:
                    try:
                        retrieval = rag.select(task, diagnostic, directory, budget.deadline,
                                               policy['validation_reserve_seconds'])
                    finally:
                        evidence_path = directory / 'retrieval.json'
                        if evidence_path.is_file():
                            evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
                            summary['rag_history'].append({k: v for k, v in evidence.items() if k != 'hits'})
                            artifacts.event('rag_search_finished', attempt=number, status=evidence['status'],
                                            category=evidence.get('category'), query=evidence['query'],
                                            mode=evidence['mode'], rag_config_sha256=rag.sha256)
                            artifacts.json('result.json', summary)
                prompt = build(task, runtime, policy, previous, diagnostic, selected_skills, raw_initial,
                               templates=templates, retrieval=retrieval)
                summary['skills_used'] |= bool(prompt.skills)
                budget.requests += 1
                summary['repair_attempts_used'] = number
                artifacts.event('generation_dispatched', attempt=number, request_number=budget.requests,
                                prompt_sha256=digest(prompt.text.encode()), messages_sha256=json_digest(prompt.messages),
                                system_prompt_sha256=digest(prompt.system.encode()),
                                prompt_templates_sha256=templates.sha256, skills=prompt.skills)
                if retrieval:
                    retrieval['injected_ids'] = prompt.context['rag_candidate_ids']
                    retrieval['injected_bytes'] = prompt.context['rag_injected_bytes']
                    if retrieval['status'] != 'skipped':
                        retrieval['status'] = 'injected' if retrieval['injected_ids'] else 'no_reference_injected'
                    artifacts.json(directory / 'retrieval.json', retrieval)
                    summary['rag_history'][-1] = {k: v for k, v in retrieval.items() if k != 'hits'}
                    artifacts.event('rag_retrieved', attempt=number, query_sha256=digest(retrieval['query'].encode()),
                                    candidate_ids=retrieval['retrieved_ids'], injected_ids=retrieval['injected_ids'],
                                    injected_bytes=retrieval['injected_bytes'], corpus_sha256=retrieval['corpus_sha256'],
                                    index_fingerprint=retrieval['index_fingerprint'],
                                    eligible_count=retrieval.get('eligible_count'),
                                    rejected_count=retrieval.get('rejected_count'),
                                    injection_policy=retrieval.get('injection_policy'),
                                    no_reference_reason=retrieval.get('no_reference_reason'))
                summary['requests'].append({'attempt': number, 'state': 'dispatched',
                                            'request_outcome_unknown': True})
                summary['generation_requests'] = budget.requests
                artifacts.json('result.json', summary)
                metadata = model.generate(prompt, runtime, directory, budget.deadline)
                summary['requests'][-1].update(state='finished', **metadata)
                if number == 0:
                    summary['stages']['generation'] = metadata
                artifacts.json('result.json', summary)
                artifacts.event('generation_finished', attempt=number, metadata=metadata)
                if metadata.get('status') != 'passed':
                    raise Failure(metadata.get('category', 'generation_error'), 'Model request failed; inspect candidate generation.log')
                source, extraction = extract_code((directory / 'response.txt').read_bytes().decode('utf-8'),
                                                  top_function=top_function, details=extraction_details)
            artifacts.json(f'candidates/{number:03d}/extraction.json', extraction_details)
            path = artifacts.bytes(f'candidates/{number:03d}/candidate.cpp', source.encode('utf-8'))
            candidate = candidates.add(number, source, path, previous.candidate_id if previous else None)
            if candidate is None:
                artifacts.event('duplicate_candidate', attempt=number, source_sha256=digest(source.encode()))
                stop = 'repeated_candidate'
                break
            artifacts.event('candidate_created', attempt=number, source_sha256=candidate.sha256,
                            parent_id=candidate.parent_id, extraction=extraction, extraction_details=extraction_details)
            if not task.stages:
                stop = 'no_validation_materials'
                break
            diagnostic = None
            for stage_index, stage in enumerate(task.stages):
                if budget.remaining() <= 0:
                    raise Failure('total_timeout', 'Budget exhausted before ' + stage)
                budget.tool_calls += 1
                artifacts.event('validation_started', attempt=number, stage=stage, source_sha256=candidate.sha256)
                if number == 0 and initial_validation is not None and stage_index < len(initial_validation):
                    outcome = ValidationResult(**initial_validation[stage_index])
                else:
                    outcome = validator.check(candidate, task, runtime, stage, budget.deadline)
                candidates.attach(candidate, outcome)
                summary['validation_history'].append({'attempt': number, **asdict(outcome)})
                summary['stages'][stage] = outcome.outcome
                artifacts.json(f'candidates/{number:03d}/validation.json', candidate.validations)
                summary.update(checks=candidate.checks, tool_calls=budget.tool_calls)
                artifacts.json('result.json', summary)
                artifacts.event('validation_finished', attempt=number, stage=stage,
                                status=outcome.outcome['status'], source_sha256=candidate.sha256)
                if outcome.outcome['status'] != 'passed':
                    diagnostic = classify(outcome)
                    break
            if diagnostic is None:
                stop = 'validation_passed'
                break
            stagnant = stagnant + 1 if diagnostic.fingerprint == last_fingerprint and candidate.rank <= last_rank else 1
            last_fingerprint, last_rank = diagnostic.fingerprint, candidate.rank
            action = decide(policy, diagnostic, number, budget, stagnant)
            artifacts.event('decision', attempt=number, action=action, diagnostic=asdict(diagnostic),
                            remaining_seconds=budget.remaining(), stagnant=stagnant)
            if not diagnostic.repairable:
                raise Failure(diagnostic.category, 'Validation infrastructure failed; no model repair')
            if action != 'repair':
                stop = action
                summary['category'] = diagnostic.category
                break
            previous = candidate
    except Failure as error:
        stop = error.category
        summary.update(category=error.category, message=str(error))
        fatal = error.category not in {'context_budget_exceeded', 'response_format_error', 'generation_incomplete'}
    except KeyboardInterrupt:
        stop, fatal = 'interrupted', True
        summary.update(category=stop, message='Interrupted by user')
    except Exception as error:
        stop, fatal = 'internal_error', True
        summary.update(category=stop, message=type(error).__name__ + ': ' + str(error))
    best = candidates.best()
    if best:
        artifacts.bytes('candidate.cpp', best.source.encode('utf-8'))
        summary.update(source='candidate.cpp', selected_candidate=best.candidate_id,
                       best_candidate=f'candidates/{best.candidate_id:03d}/candidate.cpp',
                       source_sha256=best.sha256, checks=best.checks)
        # Summary stage evidence must refer to the selected candidate, not the last attempt.
        for stage in ('csim', 'synthesis'):
            summary['stages'][stage] = next((v['outcome'] for v in best.validations if v['stage'] == stage), {'status': 'not_run'})
        artifacts.event('candidate_selected', candidate_id=best.candidate_id, source_sha256=best.sha256)
    checks = summary['checks']
    fully_passed = checks['run'] == checks['synthesize'] == 'passed'
    scope_passed = bool(task.stages) and best is not None and all(
        checks['run' if stage == 'csim' else 'synthesize'] == 'passed' for stage in task.stages)
    validation_status = 'passed' if scope_passed else 'failed' if best and best.validations else 'not_run'
    code = 1 if fatal or best is None else 0
    status = 'failed' if code else 'passed' if fully_passed else 'generated_unvalidated' if validation_status == 'not_run' or summary['validation_scope'] == 'synthesis_only' else 'generated'
    usage = {}
    for request in summary['requests']:
        for key, value in (request.get('usage') or {}).items():
            if isinstance(value, (int, float)):
                usage[key] = usage.get(key, 0) + value
    summary.update(status=status, validation_status=validation_status, stop_reason=stop,
                   generation_requests=budget.requests, api_requests_recorded=sum(r.get('requests', 0) for r in summary['requests']),
                   request_outcomes_unknown=sum(bool(r.get('request_outcome_unknown')) for r in summary['requests']),
                   request_count_note='generation_requests counts dispatched attempts conservatively; metadata records known sends.',
                   tool_calls=budget.tool_calls, usage=usage, candidates=candidates.history(),
                   elapsed_seconds=round(time.monotonic() - started, 3),
                   finished_at=datetime.now(timezone.utc).isoformat(), exit_code=code)
    artifacts.event('run_finished', status=status, stop_reason=stop, exit_code=code)
    artifacts.json('result.json', summary)
    return code, summary
