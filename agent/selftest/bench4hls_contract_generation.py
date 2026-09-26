"""Public-only contract extraction with a blind, bounded second interpretation."""
import copy
import json
from pathlib import Path
import time

from agent.core.contracts import PromptBundle, digest, json_digest
from serve.inference import Failure
from .bench4hls_architecture import interface, render
from .bench4hls_contract import VERSION, compile_contract, compare_contracts, make_vectors, public_segments
from .spec import parse_json


PROMPT = Path(__file__).parent / 'prompts/bench4hls_contract.md'
BINDING_PROMPT = Path(__file__).parent / 'prompts/bench4hls_bindings.md'
OBLIGATION_PROMPT = Path(__file__).parent / 'prompts/bench4hls_obligations.md'
RULE_PROMPT = Path(__file__).parent / 'prompts/bench4hls_rules.md'
BEHAVIOR_PROMPT = Path(__file__).parent / 'prompts/bench4hls_behaviors.md'


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def generate_contract(problem, top, output, *, model, runtime, deadline,
                      max_calls=256, seed=20260925):
    return _generate(problem, top, output, model=model, runtime=runtime, deadline=deadline,
                     max_calls=max_calls, seed=seed, representation='graph')


def generate_bindings(problem, top, output, *, model, runtime, deadline,
                      max_calls=256, seed=20260925):
    return _generate(problem, top, output, model=model, runtime=runtime, deadline=deadline,
                     max_calls=max_calls, seed=seed, representation='bindings')


def generate_obligations(problem, top, output, *, model, runtime, deadline,
                         max_calls=1024, seed=20260926):
    return _generate(problem, top, output, model=model, runtime=runtime, deadline=deadline,
                     max_calls=max_calls, seed=seed, representation='obligations')


def generate_rules(problem, top, output, *, model, runtime, deadline,
                   max_calls=1024, seed=20260927):
    return _generate(problem, top, output, model=model, runtime=runtime, deadline=deadline,
                     max_calls=max_calls, seed=seed, representation='rules')


def generate_behaviors(problem, top, output, *, model, runtime, deadline,
                       max_calls=1024, seed=20260930):
    return _generate(problem, top, output, model=model, runtime=runtime, deadline=deadline,
                     max_calls=max_calls, seed=seed, representation='behaviors')


def _generate(problem, top, output, *, model, runtime, deadline, max_calls, seed, representation):
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=False)
    result = dict(version=VERSION, status='failed', representation=representation,
                  generation_mode=representation if representation in ('bindings', 'obligations', 'rules', 'behaviors') else 'contract',
                  model_requests=0, requests=[], attempts=[], candidate_visible=False,
                  official_testbench_visible=False, semantic_correctness='unverified',
                  automatic_acceptance_allowed=False, repair_feedback_allowed=False,
                  max_calls=max_calls, sampling_seed=seed, review={'status': 'not_run'})

    def request(payload, role, attempt=0):
        if time.monotonic() >= deadline:
            raise Failure('total_timeout', 'No time remaining for ' + role)
        work = directory / f'{role}_{attempt:03d}'
        work.mkdir()
        prompt = PromptBundle(json.dumps(payload, ensure_ascii=False),
                             [dict(kind='public_problem', sha256=digest(problem))],
                             dict(phase='composable_' + representation + '_' + role, candidate_visible=False,
                                  official_testbench_visible=False,
                                  other_contract_visible=role == 'generation' and attempt > 0),
                             system=system)
        (work / 'prompt.txt').write_text(prompt.text, encoding='utf-8')
        (work / 'system_prompt.txt').write_text(system, encoding='utf-8')
        result['model_requests'] += 1
        try:
            metadata = model.generate(prompt, runtime, work, deadline)
        except (Failure, OSError, ValueError) as error:
            result['requests'].append(dict(role=role, attempt=attempt, status='failed',
                                           category=getattr(error, 'category', 'model_error'),
                                           message=str(error), request_outcome_unknown=True))
            raise
        result['requests'].append(dict(role=role, attempt=attempt, **metadata))
        if metadata.get('status') != 'passed':
            raise Failure(metadata.get('category', 'generation_error'), role + ' request failed')
        return (work / 'response.txt').read_text(encoding='utf-8'), work

    try:
        if type(max_calls) is not int or not 1 <= max_calls <= 1024 or type(seed) is not int:
            raise ValueError('Invalid call budget or sampling seed')
        public = problem.decode('utf-8')
        signature = interface(public, top)
        segments = public_segments(public)
        scope_of = lambda value: value
        if representation == 'obligations':
            from .bench4hls_obligations import compile_obligations, obligation_payload, obligation_status, evidence_mapping
            compiler, prompt_path = compile_obligations, OBLIGATION_PROMPT
            scope_of = obligation_status
        elif representation == 'rules':
            from .bench4hls_rules import compile_rules, rules_status
            from .bench4hls_obligations import obligation_payload, evidence_mapping
            compiler, prompt_path, scope_of = compile_rules, RULE_PROMPT, rules_status
        elif representation == 'behaviors':
            from .bench4hls_behaviors import compile_behaviors, behavior_status, match_behaviors
            from .bench4hls_obligations import obligation_payload, evidence_mapping
            compiler, prompt_path = compile_behaviors, BEHAVIOR_PROMPT
            scope_of = lambda value: behavior_status(value, public, top)
        elif representation == 'bindings':
            from .bench4hls_bindings import compile_bindings
            compiler, prompt_path = compile_bindings, BINDING_PROMPT
        else:
            compiler, prompt_path = compile_contract, PROMPT
        system = prompt_path.read_text(encoding='utf-8')
        result['prompt_sha256'] = digest(system.encode('utf-8'))
        result['problem_sha256'] = digest(problem)
        _write(directory / 'public_segments.json', segments)
        public_payload = dict(public_problem=public, interface=signature, public_segments=segments)
        if representation in ('obligations', 'rules', 'behaviors'):
            public_payload = obligation_payload(public, signature)
            if representation == 'rules':
                public_payload['public_input_references'] = {p['name']: p['name'] for p in signature['inputs']}
                public_payload['output_template'] = {p['name']: dict(rule=None, evidence=[], reason=None)
                                                     for p in signature['outputs']}
                public_payload.pop('component_reference_policy', None)
                public_payload['rule_language_policy'] = 'Only named unsigned sum, Boolean condition, and post-update cyclic counter. Program owns casts and component references.'
            elif representation == 'behaviors':
                public_payload['public_input_references'] = {p['name']: p['name'] for p in signature['inputs']}
                public_payload['output_template'] = {p['name']: dict(behavior=None, evidence=[], uncertainty=None)
                                                     for p in signature['outputs']}
                public_payload.pop('component_reference_policy', None)
                public_payload['behavior_extraction_policy'] = 'Describe public arithmetic, logic or state transition facts. Program decides implementability; preserve missing or ambiguous facts explicitly.'
            slices, mapping = evidence_mapping(public)
            _write(directory / 'semantic_slices.json', slices)
            _write(directory / 'evidence_mapping.json', mapping)
            _write(directory / 'output_template.json', public_payload['output_template'])
        payload = copy.deepcopy(public_payload)
        primary = None
        for attempt in range(2):
            response, work = request(payload, 'generation', attempt)
            try:
                raw = parse_json(response)
                _write(work / 'contract.json', raw)
                primary = compiler(raw, public, top)
                if representation == 'behaviors':
                    matching = match_behaviors(raw, public, top)
                    for destination in (work, directory):
                        _write(destination / 'matched_rules.json', matching['rules'])
                        _write(destination / 'capability_match.json', matching['decisions'])
                    result.update(matched_rules_sha256=json_digest(matching['rules']),
                                  capability_match_sha256=json_digest(matching['decisions']),
                                  capability_match=matching['decisions'], specification_sha256=json_digest(raw))
                if primary is not None:
                    _write(work / 'expanded_graph.json', primary.raw)
                    _write(directory / 'expanded_graph.json', primary.raw)
                    if representation in ('rules', 'behaviors'):
                        _write(work / 'lowered_obligations.json', primary.lowered_obligations_spec)
                        _write(directory / 'lowered_obligations.json', primary.lowered_obligations_spec)
                    result['expanded_graph_sha256'] = json_digest(primary.raw)
                    result['unused_public_inputs'] = getattr(primary, 'binding_unused_inputs', [])
                result['attempts'].append(dict(attempt=attempt, status='valid_schema'))
                _write(directory / 'specification.json', raw)
                break
            except (ValueError, TypeError, KeyError, RecursionError) as error:
                rejected_status = 'invalid_public_obligation' if getattr(error, 'category', '') == 'public_domain_conflict' else 'invalid_schema'
                result['attempts'].append(dict(attempt=attempt, status=rejected_status, category=getattr(error, 'category', 'schema_or_type_invalid'), message=str(error)))
                if attempt == 1:
                    raise Failure(getattr(error, 'category', 'contract_schema_invalid'), str(error)) from error
                payload = dict(public_payload, schema_feedback=dict(
                    error=str(error), previous_response=response[:32_000],
                    instruction='Correct schema/type errors using public evidence; preserve uncertainty.'))
        scope = scope_of(raw)
        from .bench4hls_obligations import public_output_coverage
        if primary is None:
            result.update(status='abstained', reasons=scope['reasons'])
            result['public_output_coverage'] = public_output_coverage(signature, None, None, 'abstain')
        else:
            vectors, coverage = make_vectors(primary, max_calls=max_calls, seed=seed,
                                             cover_counter_wrap=representation in ('obligations', 'rules', 'behaviors'))
            result['public_output_coverage'] = public_output_coverage(signature, primary, coverage, scope['decision'])
            _write(directory / 'output_obligations.json', result['public_output_coverage'])
            result['coverage'] = coverage
            _write(directory / 'vectors.json', vectors)
            _write(directory / 'coverage.json', coverage)
            if not coverage['assertions'] or not all(coverage['per_output'].values()):
                raise Failure('no_known_output_checks', 'Each declared output needs an observed known check')
            source = render(signature, vectors)
            (directory / 'selftest.cpp').write_bytes(source.encode('utf-8'))
            result.update(status='generated_unreviewed', coverage_scope=scope['decision'],
                          excluded_behaviors=scope['excluded'], source_sha256=digest(source.encode('utf-8')),
                          specification_sha256=json_digest(raw))
            # No first contract, validation, candidate or first-response feedback enters this payload.
            independent_payload = dict(public_payload, independent_extraction=True)
            try:
                response, work = request(independent_payload, 'independent')
                second_raw = parse_json(response)
                _write(work / 'contract.json', second_raw)
                second = compiler(second_raw, public, top)
                if representation == 'behaviors':
                    independent_matching = match_behaviors(second_raw, public, top)
                    _write(work / 'matched_rules.json', independent_matching['rules'])
                    _write(work / 'capability_match.json', independent_matching['decisions'])
                if second is not None:
                    _write(work / 'expanded_graph.json', second.raw)
                    if representation in ('rules', 'behaviors'):
                        _write(work / 'lowered_obligations.json', second.lowered_obligations_spec)
                if second is None:
                    second_scope = scope_of(second_raw)
                    review = dict(status='abstained', reasons=second_scope['reasons'])
                else:
                    review = compare_contracts(primary, second, max_calls=max_calls, seed=seed,
                                               cover_counter_wrap=representation in ('obligations', 'rules', 'behaviors'))
                    _, second_coverage = make_vectors(second, max_calls=max_calls, seed=seed,
                                                      cover_counter_wrap=representation in ('obligations', 'rules', 'behaviors'))
                    review.update(primary_coverage_missing=coverage['missing'],
                                  independent_coverage_missing=second_coverage['missing'],
                                  primary_specification_sha256=json_digest(raw),
                                  independent_specification_sha256=json_digest(second_raw),
                                  primary_expanded_graph_sha256=json_digest(primary.raw),
                                  independent_expanded_graph_sha256=json_digest(second.raw),
                                  problem_sha256=digest(problem), source_sha256=result['source_sha256'])
                if representation == 'behaviors':
                    review.update(independent_specification_sha256=json_digest(second_raw),
                                  independent_matched_rules_sha256=json_digest(independent_matching['rules']),
                                  independent_capability_match_sha256=json_digest(independent_matching['decisions']),
                                  capability_match=independent_matching['decisions'])
                result['review'] = review
            except (Failure, OSError, ValueError, TypeError, KeyError, RecursionError) as error:
                result['review'] = dict(status='failed', category=getattr(error, 'category', 'independent_contract_invalid'),
                                        message=str(error))
            result['review'].update(automatic_acceptance_allowed=False, repair_feedback_allowed=False)
            _write(directory / 'review.json', result['review'])
    except (Failure, OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        result.update(status='failed', category=getattr(error, 'category', 'contract_generation_error'),
                      message=str(error))
    usages = [r.get('usage') for r in result['requests']]
    result['usage_complete'] = all(isinstance(u, dict) and type(u.get('total_tokens')) is int for u in usages)
    result['total_tokens_reported'] = sum(u['total_tokens'] for u in usages if isinstance(u, dict) and type(u.get('total_tokens')) is int)
    result['receipt_id'] = json_digest(result)
    _write(directory / 'result.json', result)
    return result
