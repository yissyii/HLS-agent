"""Replay prior real-model outputs to isolate engineering changes; NOT a fresh holdout."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.artifacts.writer import Artifacts
from agent.core.contracts import digest
from agent.selftest.audit import audit_suite
from agent.selftest.generate import generate_suite, load_suite
from agent.selftest.runner import run_suite
from agent.selftest.spec import PublicTask, parse_json, require


def read(path):
    return parse_json(Path(path).read_text(encoding='utf-8'), max_bytes=32_000_000)


def replay(dataset, development, holdout, output, *, allow_execution=False, compiler='g++'):
    dataset, development, holdout = map(Path, (dataset, development, holdout))
    artifacts = Artifacts(output)
    source = {p.relative_to(ROOT).as_posix():digest(p.read_bytes())
              for p in sorted((ROOT/'agent/selftest').glob('*.py'))}
    artifacts.json('source_snapshot.json', source)
    report = dict(experiment='v0.2 retrospective replay, not fresh model generation or holdout',
                  model_requests=0, native_execution=allow_execution, development=[], audits=[],
                  limitation='Audit rule bindings selected after seeing old results; only regression evidence.')
    manifest = read(dataset/'public_manifest.json')
    fixtures = read(dataset/'fixtures.json')
    require(manifest['dataset_id'] == fixtures['dataset_id'], 'Dataset mismatch')
    private = {t['id']:t for t in fixtures['tasks']}
    for task in manifest['tasks']:
        if task['split'] != 'development':
            continue
        name = task['id']
        original = development/'suites'/name
        public = PublicTask.load(original/'problem.txt', original/'interface.h')
        require(all(public.snapshot()[k] == task[k] for k in public.snapshot()), 'Public inputs changed')
        response = {}
        hashes = {}
        for stage, key in [('contract', 'contract'), ('plan', 'test_plan')]:
            path = original/f'generation_{stage}'/'response.txt'
            response[key] = parse_json(path.read_text(encoding='utf-8'))
            hashes[stage] = digest(path.read_bytes())
        result = generate_suite(public, artifacts.root/'suites'/name, response=response,
                                planning_policy='bounded-v2', max_cases=4096)
        row = dict(id=name, original_status=read(original/'generation_result.json')['status'],
                   replay_status=result['status'], original_response_sha256=hashes, variants=[])
        if result['status'] == 'generated_unreviewed':
            row['suite_id'] = result['suite_id']
            row['case_count'] = result['coverage']['case_count']
            if allow_execution:
                for variant in private[name]['variants']:
                    path = (dataset/variant['path']).resolve()
                    require(path.is_relative_to(dataset.resolve()) and digest(path.read_bytes()) == variant['sha256'],
                            'Private source changed')
                    run = run_suite(artifacts.root/'suites'/name, path,
                        artifacts.root/'native'/name/variant['id'], compiler=compiler, allow_execution=True)
                    row['variants'].append(dict(id=variant['id'], role=variant['role'], status=run['status']))
        report['development'].append(row)
        artifacts.json('replay_result.json', report)
    for name, rule in [('count_bits8', 'popcount'), ('reverse_bits8', 'reverse_bits'), ('gray_decode8', 'gray_decode')]:
        suite = holdout/'suites'/name
        manifest, data, contract, _ = load_suite(suite)
        binding = dict(schema_version=1, suite_id=manifest['suite_id'], rule_id=rule, width=8,
                       evidence=[dict(source='problem', quote=data['problem.txt'].decode().strip())])
        artifacts.json(f'bindings/{name}.json', binding)
        audit = audit_suite(suite, artifacts.root/'audits'/name, binding=artifacts.root/'bindings'/f'{name}.json')
        report['audits'].append(dict(id=name, status=audit['status'], checks=audit['checks'],
                                    first_conflict=audit['conflicts'][0] if audit['conflicts'] else None))
    control_passed = [r for r in report['development'] if any(v['role']=='correct' and v['status']=='selftest_passed' for v in r['variants'])]
    eligible = [v for row in control_passed for v in row['variants'] if v['role']=='mutant']
    report['metrics'] = dict(tasks=len(report['development']), generated=sum(r['replay_status']=='generated_unreviewed' for r in report['development']),
        controls_passed=len(control_passed), false_positives=sum(v['role']=='correct' and v['status']=='selftest_failed' for r in report['development'] for v in r['variants']),
        eligible_mutants=len(eligible), functional_kills=sum(v['status']=='selftest_failed' for v in eligible),
        model_requests=0)
    require(source == {p.relative_to(ROOT).as_posix():digest(p.read_bytes()) for p in sorted((ROOT/'agent/selftest').glob('*.py'))},
            'Module source changed during replay')
    artifacts.json('replay_result.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('dataset', 'development', 'holdout', 'output'):
        parser.add_argument(name)
    parser.add_argument('--allow-execution', action='store_true')
    parser.add_argument('--compiler', default='g++')
    args = parser.parse_args()
    print(json.dumps(replay(args.dataset, args.development, args.holdout, args.output,
                           allow_execution=args.allow_execution, compiler=args.compiler), indent=2))
