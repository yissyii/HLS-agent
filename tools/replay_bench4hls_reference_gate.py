"""Replay frozen stress drafts through independent finite references."""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
import itertools
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.selftest.bench4hls_architecture import interface
from agent.selftest.bench4hls_reference_gate import compile_oracle, review_testbench
from tools.run_bench4hls_behavior_stress import verify_suite
from tools.bench4hls_behavior_stress_cases import step
from tools.bench4hls_behavior_stress_native import assess_controls

def sha(b):
    return hashlib.sha256(b).hexdigest()

def read(p):
    return json.loads(Path(p).read_text(encoding='utf8'))

def write(p, x):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    temporary = p.with_suffix(p.suffix + '.tmp')
    temporary.write_bytes((json.dumps(x, indent=2) + '\n').encode())
    temporary.replace(p)

def finite_table(case, source_sha256, review_record=None):
    p = case['parameters']
    if case['expected_scope'] != 'supported' or p['kind'] not in ('sum', 'logic', 'counter'):
        raise ValueError('Unsupported reference adapter case')
    signature = interface(case['public_problem'], case['top'])
    ins, outs = signature['inputs'], signature['outputs']
    if (any(port['signed'] for port in ins + outs) or sum(port['width'] for port in ins) > 12
            or p['kind'] == 'counter' and (len(outs) != 1 or outs[0]['width'] > 7)):
        raise ValueError('Existing fixture adapter domain limit')
    states = ['unit'] if p['kind'] in ('sum', 'logic') else ['unknown'] + ['q' + str(i) for i in range(1 << outs[0]['width'])]
    transitions = []
    for state in states:
        current = None if state == 'unknown' else None if state == 'unit' else int(state[1:])
        for values in itertools.product(*[range(1 << x['width']) for x in ins]):
            values = list(values)
            output, nxt = step(case, values, current)
            ns = 'unit' if state == 'unit' else 'unknown' if nxt is None else 'q' + str(nxt)
            transitions.append({'state': state, 'inputs': values, 'outputs': {o['name']: output.get(o['name']) for o in outs}, 'next_state': ns})
    table = {'schema_version': 1, 'problem_sha256': sha(case['public_problem'].encode()), 'top': case['top'], 'inputs': [{'name': x['name'], 'width': x['width'], 'signed': False} for x in ins], 'outputs': [{'name': x['name'], 'width': x['width'], 'signed': False} for x in outs], 'states': states, 'initial_state': 'unit' if states == ['unit'] else 'unknown', 'transitions': transitions, 'provenance': {'origin': 'evaluation_fixture', 'reference_id': case['case_id'], 'source_sha256': source_sha256, 'review_status': 'fixture_qualified' if review_record else 'unreviewed', 'review_record': review_record}}
    compile_oracle(table, case['public_problem'].encode(), case['top'])
    return table

def qualification_ok(path, suite_hash):
    q = read(path)
    controls = Path(path).parent
    cm = read(controls / 'package_manifest.json')
    return q == assess_controls(controls) and q.get('status') == 'qualified' and (cm.get('suite_manifest_sha256') == suite_hash)

def replay(suite, runs, output, qualification):
    suite, output, qualification = (Path(p).resolve() for p in (suite, output, qualification))
    runs = [Path(p).resolve() for p in runs]
    if output.exists():
        raise ValueError('Output already exists')
    sm = verify_suite(suite)
    sh = sha((suite / 'manifest.json').read_bytes())
    if not qualification_ok(qualification, sh):
        raise ValueError('qualification_not_bound_to_suite_or_not_complete')
    if not runs or len(set(runs)) != len(runs):
        raise ValueError('duplicate_run')
    run_info = []
    for run in map(Path, runs):
        rm, summary, audit = (read(run / 'manifest.json'), read(run / 'summary.json'), read(run / 'audit_v1.json'))
        if rm.get('representation') not in ('rules', 'behaviors') or type(rm.get('repeats')) is not int or (not 1 <= rm['repeats'] <= 5):
            raise ValueError('invalid_run_manifest')
        if summary.get('status') != 'completed' or rm.get('suite_manifest_sha256') != sh or audit.get('suite_manifest_sha256') != sh or (audit.get('run_manifest_sha256') != sha((run / 'manifest.json').read_bytes())) or audit.get('report_integrity_errors') != []:
            raise ValueError('invalid_run')
        planned = {(c['case_id'], i) for c in sm['cases'] for i in range(1, rm['repeats'] + 1)}
        if {(x['case_id'], x['run_index']) for x in audit['rows']} != planned or len(audit['rows']) != len(planned) or {(x['task_id'], x['run_index']) for x in summary['attempts']} != planned:
            raise ValueError('run_denominator_mismatch')
        if len(summary['attempts']) != len(planned) or summary.get('total') != len(planned) or summary.get('finished') != len(planned):
            raise ValueError('summary_denominator_mismatch')
        run_info.append((run, rm, summary, audit))
    source_names = sorted(set(sm['source_hashes']) | {
        'agent/selftest/bench4hls_reference_gate.py', 'tools/review_bench4hls_testbench.py',
        'tools/replay_bench4hls_reference_gate.py', 'tools/test_bench4hls_reference_gate.py',
        'tools/test_bench4hls_reference_gate_adapter.py'})
    source_hashes = {}
    for name in source_names:
        p = ROOT / name
        if not p.is_file():
            raise ValueError('source_missing:' + name)
        got = sha(p.read_bytes())
        wanted = sm['source_hashes'].get(name)
        if wanted and wanted != got:
            raise ValueError('frozen_source_drift:' + name)
        source_hashes[name] = got
    output.mkdir(parents=True)
    (output / 'sources').mkdir()
    for name in source_names:
        dest = output / 'sources' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, dest)
        if sha(dest.read_bytes()) != source_hashes[name]:
            raise ValueError('source_snapshot_drift:' + name)
    manifest = {'version': 1, 'suite_manifest_sha256': sh, 'qualification_sha256': sha(Path(qualification).read_bytes()), 'runs': [{'path': str(r), 'manifest_sha256': sha((r / 'manifest.json').read_bytes()), 'summary_sha256': sha((r / 'summary.json').read_bytes()), 'audit_sha256': sha((r / 'audit_v1.json').read_bytes())} for r, _, _, _ in run_info], 'source_hashes': source_hashes, 'model_calls': 0}
    manifest.update(suite_path=str(suite), qualification_path=str(qualification),
                    reference_source_sha256=sha((suite / 'reference/cases.json').read_bytes()),
                    plans=[dict(run=str(run), representation=rm['representation'], case_id=c['case_id'], run_index=i)
                           for run, rm, _, _ in run_info for c in sm['cases'] for i in range(1, rm['repeats'] + 1)])
    inputs = output / 'inputs'
    inputs.mkdir()
    shutil.copyfile(suite / 'manifest.json', inputs / 'suite_manifest.json')
    shutil.copyfile(suite / 'reference/cases.json', inputs / 'reference_cases.json')
    shutil.copyfile(qualification, inputs / 'control_qualification.json')
    for run, _, _, _ in run_info:
        target = inputs / sha(str(run).encode())[:12]
        target.mkdir()
        for name in ('manifest.json', 'summary.json', 'audit_v1.json'):
            shutil.copyfile(run / name, target / name)
    write(output / 'manifest.json', manifest)
    cases = {x['case_id']: x for x in read(suite / 'reference/cases.json')}
    source_sha = sha((suite / 'reference/cases.json').read_bytes())
    rows = []
    summary = {'status': 'running', 'suite_manifest_sha256': sh, 'qualification_sha256': sha(Path(qualification).read_bytes()), 'runs': [str(Path(x)) for x in runs], 'rows': rows, 'status_counts': {}, 'by_representation': {}, 'model_requests_this_replay': 0, 'automatic_acceptance_allowed': False, 'known_regression_not_heldout': True}
    summary.update(manifest_sha256=sha((output / 'manifest.json').read_bytes()),
                   total_attempts=len(manifest['plans']), finished=0, repair_feedback_allowed=False)
    write(output / 'summary.json', summary)
    for run, rm, run_summary, audit in run_info:
        for cmeta in sm['cases']:
            c = cases[cmeta['case_id']]
            for i in range(1, rm['repeats'] + 1):
                gen = run / c['case_id'] / ('run_%03d' % i) / 'generation'
                target = output / rm.get('representation', 'unknown') / sha(str(run.resolve()).encode())[:12] / c['case_id'] / ('run_%03d' % i)
                audit_row = next((x for x in audit['rows'] if x['case_id'] == c['case_id'] and x['run_index'] == i))
                row = {'case_id': c['case_id'], 'run_index': i, 'representation': rm.get('representation'), 'expected_scope': cmeta['expected_scope'], 'generation_status': audit_row.get('generation_status')}
                target.mkdir(parents=True, exist_ok=False)
                if cmeta['expected_scope'] == 'supported':
                    oracle = target / 'oracle.json'
                    table = finite_table(c, source_sha, 'sha256:' + sha(qualification.read_bytes()))
                    write(oracle, table)
                    report = review_testbench(c['public_problem'].encode(), c['top'], gen, target / 'review', oracle_path=oracle, expected_oracle_sha256=sha(oracle.read_bytes()))
                else:
                    report = review_testbench(c['public_problem'].encode(), c['top'], gen, target / 'review')
                row.update(status=report['status'], report=str((target / 'review/report.json').relative_to(output)),
                           report_sha256=sha((target / 'review/report.json').read_bytes()),
                           export_for_review_allowed=report['export_for_review_allowed'])
                rows.append(row)
                summary['status_counts'][row['status']] = summary['status_counts'].get(row['status'], 0) + 1
                summary['by_representation'].setdefault(row['representation'], {})
                summary['by_representation'][row['representation']][row['status']] = summary['by_representation'][row['representation']].get(row['status'], 0) + 1
                summary['finished'] = len(rows)
                write(output / 'summary.json', summary)
    summary['status'] = 'completed'
    if any(sha((ROOT / name).read_bytes()) != wanted for name, wanted in source_hashes.items()):
        summary['status'] = 'invalidated_source_drift'
    write(output / 'summary.json', summary)
    return summary

def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--suite', type=Path, required=True)
    p.add_argument('--run', type=Path, action='append', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--qualification', type=Path, required=True)
    a = p.parse_args(argv)
    result = replay(a.suite, a.run, a.output, a.qualification)
    print(json.dumps({key: result[key] for key in ('status', 'total_attempts', 'status_counts', 'by_representation')}))
    return 0 if result['status'] == 'completed' else 1
if __name__ == '__main__':
    raise SystemExit(main())
