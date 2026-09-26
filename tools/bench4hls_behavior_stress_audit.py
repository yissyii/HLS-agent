"""Offline stress evaluation against independent, frozen public references."""
from collections import Counter
from pathlib import Path
import hashlib
import json

from agent.core.contracts import json_digest
from agent.selftest.bench4hls_architecture import interface, render
from tools import audit_bench4hls_obligations_pilot as replay
from tools.bench4hls_behavior_stress_cases import probes, step, mutation_names


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def trace_audit(case, rows):
    state = None
    result = dict(calls=0, assertions=0, wrong=0, missing=0, unsafe_assertions=0)
    try:
        for row in rows:
            wanted, state = step(case, row['inputs'], state)
            got = row['expected']
            result['calls'] += 1
            result['assertions'] += len(got)
            for name, value in got.items():
                if name not in wanted:
                    result['wrong'] += 1
                    result['unsafe_assertions'] += 1
                elif value != wanted[name]:
                    result['wrong'] += 1
            result['missing'] += len(set(wanted)-set(got))
        result['status'] = ('counterexample' if result['wrong'] else 'partial' if result['missing']
                            else 'consistent_on_audit' if result['assertions'] else 'no_checks')
    except (ValueError, TypeError, KeyError, IndexError, ArithmeticError) as exc:
        result.update(status='audit_error', message=str(exc))
    return result


def probe_audit(case, contract):
    if contract is None:
        return {'status': 'abstained'}
    try:
        state, rows = contract.initial_state(), []
        for inputs in probes(case):
            expected, state, _ = contract.step(inputs, state)
            rows.append(dict(inputs=inputs, expected=expected))
        return trace_audit(case, rows)
    except (ValueError, TypeError, KeyError, IndexError, ArithmeticError) as exc:
        return dict(status='audit_error', message=str(exc))


def mutations_audit(case, rows, invalid=False):
    result = {}
    for name in mutation_names(case):
        record = dict(eligible=False, status='not_run' if rows is None else 'invalid_test' if invalid else 'survived',
                      differing_assertions=0)
        if rows is not None and not invalid:
            good_state = bad_state = None
            try:
                for row in rows:
                    good, good_state = step(case, row['inputs'], good_state)
                    bad, bad_state = step(case, row['inputs'], bad_state, name)
                    for output in set(good) & set(bad) & set(row['expected']):
                        if good[output] != bad[output]:
                            record['differing_assertions'] += 1
                if record['differing_assertions']:
                    record.update(eligible=True, status='killed')
            except (ValueError, TypeError, KeyError, IndexError, ArithmeticError) as exc:
                record.update(status='audit_error', message=str(exc))
        result[name] = record
    return result


def _hash_binding(data, expected, errors, label, required=False):
    if expected is None:
        if required:
            errors.append(label + '_unbound')
    elif data is None or json_digest(data) != expected:
        errors.append(label + '_sha256')


def extraction(case, representation, directory, gen, errors, independent=False):
    """Recompile raw extraction and bind each stored intermediate artifact."""
    role = 'independent' if independent else 'primary'
    base = directory/'independent_000' if independent else directory
    declaration_path = base/('contract.json' if independent else 'specification.json')
    receipt = gen.get('review', {}) if independent else gen
    status = receipt.get('status', 'not_run')
    valid = status not in ('not_run', 'failed', 'missing')
    graph_expected = receipt.get('independent_expanded_graph_sha256' if independent else 'expanded_graph_sha256')
    raw = replay.artifact(declaration_path, errors, role+'_specification', valid or bool(graph_expected))
    if raw is None:
        return {'status': 'unavailable'}, None
    specification_hash = receipt.get('independent_specification_sha256' if independent else 'specification_sha256')
    _hash_binding(raw, specification_hash, errors, role+'_specification',
                  required=valid and (representation == 'behaviors' or status == 'generated_unreviewed'))
    # A failed blind extraction may preserve a malformed response. That is a
    # recorded generation failure, not unexplained artifact corruption.
    if not valid and not graph_expected and not (base/'expanded_graph.json').is_file():
        return {'status': 'failed_extraction'}, None
    try:
        contract = replay.replay_frontend(representation, raw, case['public_problem'], case['top'])
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        errors.append(role+'_replay_invalid:'+str(exc))
        return dict(status='invalid_declaration', message=str(exc)), None
    graph = replay.artifact(base/'expanded_graph.json', errors, role+'_expanded_graph', contract is not None)
    _hash_binding(graph, graph_expected, errors, role+'_expanded_graph', required=valid and contract is not None)
    lowered = replay.artifact(base/'lowered_obligations.json', errors, role+'_lowered_obligations', contract is not None)
    matched = capability = None
    if representation == 'behaviors':
        matched = replay.artifact(base/'matched_rules.json', errors, role+'_matched_rules')
        capability = replay.artifact(base/'capability_match.json', errors, role+'_capability_match')
        prefix = 'independent_' if independent else ''
        _hash_binding(matched, receipt.get(prefix+'matched_rules_sha256'), errors, role+'_matched_rules', valid)
        _hash_binding(capability, receipt.get(prefix+'capability_match_sha256'), errors, role+'_capability_match', valid)
        if capability != receipt.get('capability_match'):
            errors.append(role+'_capability_receipt_mismatch')
    if contract is not None and graph is not None:
        replay.replay_artifacts(representation, raw, graph, lowered, matched, capability,
                                case['public_problem'], case['top'], errors, role)
    elif contract is None:
        if graph is not None:
            errors.append(role+'_abstention_has_graph')
        if representation == 'behaviors':
            replay.replay_behavior_abstention(raw, matched, capability, case['public_problem'], case['top'], errors, role)
    if status == 'abstained' and contract is not None:
        errors.append(role+'_expected_abstention')
    return probe_audit(case, contract), contract


def audit_case(case, representation, directory, run_index):
    errors = []
    outer = replay.artifact(directory/'result.json', errors, 'outer') or {}
    gen = replay.artifact(directory/'generation/result.json', errors, 'generation', bool(outer.get('generation'))) or {}
    if gen and outer.get('generation') != gen:
        errors.append('outer_generation_mismatch')
    if outer.get('task_id') != case['case_id'] or outer.get('run_index') != run_index:
        errors.append('outer_identity_mismatch')
    if outer and outer.get('representation') != representation:
        errors.append('outer_representation_mismatch')
    if gen:
        receipt = dict(gen)
        receipt.pop('receipt_id', None)
        if gen.get('receipt_id') != json_digest(receipt):
            errors.append('generation_receipt_id')
        if gen.get('problem_sha256') and gen['problem_sha256'] != hashlib.sha256(case['public_problem'].encode()).hexdigest():
            errors.append('generation_problem_sha256')
        if gen.get('representation') != representation:
            errors.append('generation_representation')
    status = gen.get('status', 'missing')
    primary, contract = extraction(case, representation, directory/'generation', gen, errors)
    independent, _ = extraction(case, representation, directory/'generation', gen, errors, independent=True)
    vectors = replay.artifact(directory/'generation/vectors.json', errors, 'vectors', status == 'generated_unreviewed')
    va = trace_audit(case, vectors) if vectors is not None else {'status':'unavailable'}
    source = directory/'generation/selftest.cpp'
    source_hash = sha(source) if source.is_file() else None
    if status == 'generated_unreviewed' or source.is_file():
        try:
            rendered = render(interface(case['public_problem'],case['top']), vectors).encode('utf-8')
            if not source.is_file() or source.read_bytes() != rendered or gen.get('source_sha256') != source_hash:
                errors.append('selftest_render_or_hash')
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            errors.append('selftest_render:'+str(exc))
    if status == 'abstained' and source.is_file():
        errors.append('abstention_has_selftest')
    coverage = gen.get('public_output_coverage', {})
    qualified = (case['expected_scope']=='supported' and outer.get('status')=='completed'
                 and status=='generated_unreviewed' and not errors and gen.get('coverage_scope')=='ready'
                 and coverage.get('complete') is True and primary.get('status')=='consistent_on_audit'
                 and va.get('status')=='consistent_on_audit')
    invalid = bool(errors) or primary.get('status') in ('counterexample','audit_error','invalid_declaration') or va.get('status') in ('counterexample','audit_error')
    return dict(case_id=case['case_id'], family=case['family'], run_index=run_index,
                expected_scope=case['expected_scope'], status=outer.get('status','missing'), generation_status=status,
                model_requests=gen.get('model_requests',outer.get('model_requests',0)),
                total_tokens_reported=gen.get('total_tokens_reported',outer.get('total_tokens_reported',0)),
                usage_complete=gen.get('usage_complete',False), review=gen.get('review',{}),
                public_output_complete=coverage.get('complete',False), coverage_scope=gen.get('coverage_scope'),
                primary_audit=primary, vector_audit=va, independent_audit=independent,
                mutants=mutations_audit(case,vectors,invalid), errors=errors, selftest_sha=source_hash,
                bounded_python_qualified=bool(qualified))


def aggregate(rows):
    supported = [r for r in rows if r['expected_scope']=='supported']
    negative = [r for r in rows if r['expected_scope']!='supported']
    return dict(opportunities=len(rows), supported_attempts=len(supported), negative_attempts=len(negative),
                generation_status_counts=dict(Counter(r['generation_status'] for r in rows)),
                bounded_python_qualified=sum(r['bounded_python_qualified'] for r in supported),
                negative_explicit_abstained=sum(r['generation_status']=='abstained' and not r['errors'] for r in negative),
                negative_failed_no_test=sum(r['generation_status'] in ('failed','missing') and r['selftest_sha'] is None for r in negative),
                generated_on_out_of_scope=sum(r['generation_status']=='generated_unreviewed' for r in negative),
                unsafe_assertions=sum(r[role].get('unsafe_assertions',0) for r in rows for role in ('primary_audit','vector_audit')),
                integrity_failures=sum(bool(r['errors']) for r in rows),
                mutation_opportunities=sum(len(r['mutants']) for r in supported),
                mutant_statuses=dict(Counter(v['status'] for r in rows for v in r['mutants'].values())),
                model_requests=sum(r['model_requests'] for r in rows),
                total_tokens_reported=sum(r['total_tokens_reported'] for r in rows),
                usage_complete=all(r['usage_complete'] for r in rows))


def audit_run(suite, run, manifest):
    rm, summary = read(run/'manifest.json'), read(run/'summary.json')
    errors = []
    if rm.get('representation') not in ('rules','behaviors') or type(rm.get('repeats')) is not int or not 1 <= rm['repeats'] <= 5:
        raise ValueError('Invalid stress run manifest')
    if rm.get('suite_manifest_sha256') != sha(suite/'manifest.json'):
        errors.append('suite_manifest_mismatch')
    cases = {c['case_id']:c for c in read(suite/'reference/cases.json')}
    planned = {(c['case_id'],i) for c in manifest['cases'] for i in range(1,rm['repeats']+1)}
    actual = {(r['task_id'],r['run_index']) for r in summary['attempts']}
    if actual != planned or len(summary['attempts']) != len(planned) or summary.get('status')!='completed' or summary.get('finished')!=len(planned) or summary.get('total')!=len(planned):
        errors.append('attempt_denominator_incomplete')
    summary_payload = dict(summary)
    receipt = summary_payload.pop('receipt_id',None)
    if receipt != hashlib.sha256(json.dumps(summary_payload,sort_keys=True,separators=(',',':')).encode()).hexdigest():
        errors.append('summary_receipt_mismatch')
    summary_rows = {(r['task_id'],r['run_index']):r for r in summary['attempts']}
    rows = []
    for item in manifest['cases']:
        for index in range(1,rm['repeats']+1):
            row = audit_case(cases[item['case_id']],rm['representation'],run/item['case_id']/('run_%03d'%index),index)
            compact = summary_rows.get((item['case_id'],index),{})
            if compact.get('integrity_error') or any(compact.get(k)!=row[k] for k in ('status','model_requests','total_tokens_reported')):
                row['errors'].append('summary_outer_mismatch')
                row['bounded_python_qualified'] = False
            if errors:
                row['errors'].extend(errors)
                row['bounded_python_qualified'] = False
            rows.append(row)
    return dict(suite=str(suite),run=str(run),suite_manifest_sha256=sha(suite/'manifest.json'),
                run_manifest_sha256=sha(run/'manifest.json'),report_integrity_errors=errors,
                audit_source_sha256=sha(Path(__file__)),rows=rows,summary=aggregate(rows),
                by_scope={scope:aggregate([r for r in rows if r['expected_scope']==scope])
                          for scope in sorted({r['expected_scope'] for r in rows})},
                by_case={name:aggregate([r for r in rows if r['case_id']==name]) for name in sorted(cases)})
