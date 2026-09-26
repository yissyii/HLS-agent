"""Public-only validation planning: explicit scope before testbench code generation."""
from pathlib import Path
import json
import re
import time

from agent.core.contracts import PromptBundle, digest, json_digest
from serve.inference import Failure
from .spec import keys, parse_json, require, text


PLAN_PROMPT = Path(__file__).parent/'prompts/bench4hls_semantics.md'


def validate_plan(raw, problem, top_function):
    keys(raw, {'schema_version', 'decision', 'prototype', 'state_model', 'checks',
               'excluded', 'blockers'}, 'semantic plan')
    require(type(raw['schema_version']) is int and raw['schema_version'] == 1, 'Invalid plan version')
    require(raw['decision'] in {'ready', 'partial', 'abstain'}, 'Invalid plan decision')
    text(raw['state_model'], 'state model', 3000)
    require(isinstance(raw['checks'], list) and len(raw['checks']) <= 12, 'Invalid checks')
    for name in ('excluded', 'blockers'):
        require(isinstance(raw[name], list) and len(raw[name]) <= 12, 'Invalid '+name)
        for value in raw[name]:
            text(value, name, 2000)
    if raw['prototype'] is not None:
        prototype = text(raw['prototype'], 'prototype', 4000).rstrip().rstrip(';')
        compact = lambda value: re.sub(r'\s+', '', value)
        require(compact(prototype) in compact(problem), 'Prototype not grounded in public text')
        require(re.search(r'\b'+re.escape(top_function)+r'\s*\(', prototype), 'Wrong top function')
    ids = []
    for check in raw['checks']:
        keys(check, {'id', 'claim', 'evidence', 'stimulus', 'oracle', 'comparison'}, 'planned check')
        require(isinstance(check['id'], str) and re.fullmatch(r'C[0-9]+', check['id']), 'Invalid check ID')
        ids.append(check['id'])
        for name in ('claim', 'stimulus', 'oracle', 'comparison'):
            text(check[name], name, 3000)
        require(isinstance(check['evidence'], list) and 1 <= len(check['evidence']) <= 4,
                'Check requires public evidence')
        for quote in check['evidence']:
            text(quote, 'evidence', 4000)
            require(quote in problem, 'Evidence not found in public problem')
    require(len(ids) == len(set(ids)), 'Duplicate check IDs')
    if raw['decision'] == 'abstain':
        require(not raw['checks'] and raw['blockers'], 'Abstention requires blockers and no safe checks')
    else:
        require(raw['prototype'] is not None and raw['checks'] and not raw['blockers'],
                'Ready/partial plan requires an interface and executable checks')
        require(bool(raw['excluded']) == (raw['decision'] == 'partial'),
                'Excluded behaviors must be declared as partial coverage')
    # Grounded quotes establish provenance, not semantic correctness.
    return raw


def plan_testbench(problem, top_function, output, *, model, runtime, deadline):
    directory = Path(output); directory.mkdir(parents=True, exist_ok=False)
    system = PLAN_PROMPT.read_text(encoding='utf-8')
    payload = dict(public_problem=problem.decode('utf-8'), public_top_function=top_function)
    result = dict(status='failed', model_requests=0, requests=[], plan=None,
                  semantic_correctness='unverified', official_testbench_visible=False,
                  candidate_visible=False, prompt_sha256=digest(system.encode('utf-8')))
    # A second call reviews a proposed abstention or invalid plan, not the candidate.
    for attempt in range(2):
        work = directory/f'{attempt:03d}'; work.mkdir()
        try:
            if time.monotonic() >= deadline:
                raise Failure('total_timeout', 'No time for public semantic planning')
            prompt = PromptBundle(json.dumps(payload, ensure_ascii=False),
                [dict(kind='public_problem', sha256=digest(problem))],
                dict(phase='selftest_semantic_plan', attempt=attempt,
                     official_testbench_visible=False, candidate_visible=False), system=system)
            (work/'prompt.txt').write_text(prompt.text, encoding='utf-8')
            (work/'system_prompt.txt').write_text(system, encoding='utf-8')
            result['model_requests'] += 1
            metadata = model.generate(prompt, runtime, work, deadline)
            result['requests'].append(metadata)
            if metadata.get('status') != 'passed':
                raise Failure(metadata.get('category', 'generation_error'), 'Semantic planning request failed')
            raw = parse_json((work/'response.txt').read_text(encoding='utf-8'))
            plan = validate_plan(raw, payload['public_problem'], top_function)
            (work/'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            if plan['decision'] != 'abstain' or attempt == 1:
                result.update(status='abstained' if plan['decision']=='abstain' else 'planned', plan=plan)
                result.pop('category', None)
                result.pop('message', None)
                break
            payload['review'] = dict(proposed_plan=plan,
                instruction='Review the blockers against the public text. Search for bounded, meaningful partial checks. '
                            'Correct false language/interface assumptions. Preserve abstention if no sound check exists. '
                            'Do not invent missing semantics or change the public prototype.')
        except Failure as error:
            result.update(category=error.category, message=str(error)); break
        except (OSError, ValueError, TypeError, KeyError) as error:
            result.update(category='semantic_plan_invalid', message=str(error))
            payload['review'] = dict(validation_error=str(error), instruction='Return a valid grounded plan.')
    result['receipt_id'] = json_digest(result)
    (directory/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return result
