"""Replay frozen known failures against the hardened auditor, not a new blind study."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.artifacts.writer import Artifacts
from agent.core.contracts import digest, json_digest
from agent.selftest.audit import audit_suite
from tools.evaluate_selftest_auditor import snapshot


def run(baseline, output):
    baseline = Path(baseline).resolve()
    artifacts = Artifacts(output)
    start = time.monotonic()
    old = json.loads((baseline/'evaluation.json').read_text())
    protocol = json.loads((baseline/'protocol.json').read_text())
    protocol_id = protocol.pop('protocol_id')
    if not old['complete'] or old['protocol_id'] != protocol_id or json_digest(protocol) != protocol_id:
        raise ValueError('Require complete, internally consistent frozen baseline')
    truth = {v['id']:v for v in map(json.loads, (baseline/'truth.jsonl').read_text().splitlines())}
    for row in old['cases']:
        if truth.get(row['id']) != row['truth']:
            raise ValueError('Baseline truth label mismatch')
    frozen = snapshot()
    frozen[Path(__file__).relative_to(ROOT).as_posix()] = digest(Path(__file__).read_bytes())
    # Hash all original evidence; never modify it. This is an integrity check,
    # not a signature from an independent evaluator.
    baseline_hashes = {p.relative_to(baseline).as_posix():digest(p.read_bytes())
                       for p in sorted(baseline.rglob('*')) if p.is_file()}
    artifacts.json('baseline_hashes.json', baseline_hashes)
    artifacts.json('source_hashes.json', frozen)
    for name in frozen:
        artifacts.bytes('source/'+name, (ROOT/name).read_bytes())
    report = dict(schema_version=1, complete=False, evaluation_kind='known_case_regression',
                  baseline=str(baseline), baseline_protocol_id=protocol_id, model_requests=0,
                  cases=[], explicit_budget_rechecks=[], semantic_binding_challenges=[], applicability=[], failures=[])

    def persist():
        report['elapsed_seconds'] = round(time.monotonic()-start, 3)
        artifacts.json('recheck.json', report)

    def check(name, suite_name, binding_path, expected, *, max_cases=None):
        kwargs = {} if max_cases is None else {'max_cases': max_cases}
        at = time.monotonic()
        result = audit_suite(baseline/'suites'/suite_name, artifacts.root/'audits'/name,
                             binding=binding_path, **kwargs)
        value = dict(id=name, expected=expected, result=result, seconds=round(time.monotonic()-at, 6))
        if result['status'] != expected:
            report['failures'].append(dict(id=name, expected=expected, actual=result['status']))
        if (result['automatic_repair_allowed'] or not result['manual_review_required']
                or result['binding_semantics'] != 'not_independently_verified'):
            report['failures'].append(dict(id=name, error='Unsafe semantics/repair metadata'))
        if result.get('max_cases') != (65536 if max_cases is None else max_cases):
            report['failures'].append(dict(id=name, error='Budget mismatch'))
        return value

    try:
        for row in old['cases']:
            if row['label'] not in ('correct', 'incorrect'):
                continue
            name = row['id']
            bound = baseline/'bindings'/f'{name}_default.json'
            expected = 'supported' if row['label']=='correct' else 'conflict'
            value = check(name, name, bound, expected)
            value['label'] = row['label']
            if value['result']['evidence_scope'] != 'full_domain_rule' or not value['result']['audit_complete']:
                report['failures'].append(dict(id=name, error='Default audit did not complete full domain'))
            report['cases'].append(value)
            if row['label']=='incorrect' and row['audits']['default']['status']=='supported':
                sampled = check(name+'_explicit4096', name, bound, 'supported', max_cases=4096)
                if sampled['result']['evidence_scope'] != 'sampled_rule':
                    report['failures'].append(dict(id=sampled['id'], error='Sampled evidence mislabeled'))
                report['explicit_budget_rechecks'].append(sampled)
            persist()
            print(f'{len(report["cases"])}/146 {name}: {value["result"]["status"]}', flush=True)
        for row in old['semantic_binding_challenges']:
            name = row['id']
            report['semantic_binding_challenges'].append(check(name,name,baseline/'bindings'/f'{name}.json',row['result']['status']))
        for row in old['applicability']:
            name = row['id']
            binding = None if name=='missing_binding' else baseline/'bindings'/f'{name}.json'
            report['applicability'].append(check(name,name,binding,'inconclusive',max_cases=1 if name=='budget_too_small' else None))
        after = {p.relative_to(baseline).as_posix():digest(p.read_bytes())
                 for p in sorted(baseline.rglob('*')) if p.is_file()}
        report['baseline_unchanged'] = after == baseline_hashes
        report['source_unchanged'] = all(digest((ROOT/name).read_bytes()) == hashed for name,hashed in frozen.items())
        if not report['baseline_unchanged'] or not report['source_unchanged']:
            report['failures'].append(dict(error='Frozen baseline or source changed'))
        report['metrics'] = dict(
            correct_total=sum(r['label']=='correct' for r in report['cases']),
            correct_supported=sum(r['label']=='correct' and r['result']['status']=='supported' for r in report['cases']),
            incorrect_total=sum(r['label']=='incorrect' for r in report['cases']),
            incorrect_conflict=sum(r['label']=='incorrect' and r['result']['status']=='conflict' for r in report['cases']),
            applicability_inconclusive=sum(r['result']['status']=='inconclusive' for r in report['applicability']))
        report['complete'] = True
    except Exception as error:
        report['failures'].append(dict(exception=type(error).__name__, message=str(error)))
        raise
    finally:
        persist()
    print(json.dumps({k:v for k,v in report.items() if k not in ('cases','explicit_budget_rechecks','semantic_binding_challenges','applicability')},indent=2))
    return 0 if not report['failures'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline')
    parser.add_argument('output')
    args = parser.parse_args()
    raise SystemExit(run(args.baseline, args.output))
