"""Evaluate FROZEN suites on independently stored correct/mutant implementations."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.artifacts.writer import Artifacts
from agent.selftest.generate import load_suite
from agent.selftest.runner import run_suite
from agent.selftest.spec import identifier, require
from serve.inference import load_config


def evaluate(fixtures, output, *, suite_root=None, split='development', **options):
    root = Path(fixtures).resolve()
    suite_root = Path(suite_root).resolve() if suite_root else root / 'suites'
    manifest = json.loads((root / 'fixtures.json').read_text(encoding='utf-8'))
    tasks = [t for t in manifest['tasks'] if split == 'all' or t['split'] == split]
    require(bool(tasks), 'No tasks selected')
    require(len({t['id'] for t in tasks}) == len(tasks), 'Duplicate task IDs')
    # Freeze all suite identities BEFORE opening any implementation source.
    frozen = {identifier(t['id']): load_suite(suite_root / t['id'])[0] for t in tasks}
    artifacts = Artifacts(output)
    artifacts.json('frozen_suites.json', frozen)
    rows = []
    for task in tasks:
        variants = task['variants']
        require(sum(v['role'] == 'correct' for v in variants) == 1, 'Exactly one correct control per task required')
        require(len({v['id'] for v in variants}) == len(variants), 'Duplicate variant IDs')
        for variant in sorted(variants, key=lambda v: v['role'] != 'correct'):
            identifier(variant['id'])
            require(variant['role'] in {'correct', 'mutant'}, 'Unknown variant role')
            source = (root / variant['path']).resolve()
            require(source.is_relative_to(root), 'Fixture source escapes root')
            suite = suite_root / task['id']
            require(load_suite(suite)[0]['suite_id'] == frozen[task['id']]['suite_id'], 'Suite changed after freeze')
            result = run_suite(suite, source, artifacts.root / task['id'] / variant['id'], **options)
            rows.append(dict(task=task['id'], split=task['split'], variant=variant['id'], role=variant['role'],
                             status=result['status'], mismatch_count=result['mismatch_count'],
                             elapsed_seconds=result['elapsed_seconds'], suite_id=result.get('suite_id'),
                             candidate_sha256=result.get('candidate_sha256')))
            artifacts.json('progress.json', rows)
    correct = [r for r in rows if r['role'] == 'correct']
    mutants = [r for r in rows if r['role'] == 'mutant']
    accepted = {r['task'] for r in correct if r['status'] == 'selftest_passed'}
    eligible = [r for r in mutants if r['task'] in accepted]
    complete_statuses = {'selftest_passed', 'selftest_failed'}
    # A compile error, timeout or invalid test is NEVER a functional mutation kill.
    kills = sum(r['status'] == 'selftest_failed' for r in eligible)
    def ratio(n, d):
        return n / d if d else None
    result = dict(schema_version=1, split=split, tasks=len(tasks), rows=rows,
        generation_origins=sorted({m['origin'] for m in frozen.values()}),
        correct_controls=len(correct), correct_passes=len(accepted),
        false_positives=sum(r['status'] == 'selftest_failed' for r in correct),
        false_positive_rate=ratio(sum(r['status'] == 'selftest_failed' for r in correct), len(correct)),
        completed_control_rate=ratio(sum(r['status'] in complete_statuses for r in correct), len(correct)),
        control_inconclusive=sum(r['status'] not in complete_statuses for r in correct),
        mutants_total=len(mutants), eligible_mutants=len(eligible), functional_kills=kills,
        excluded_mutants_failed_control=len(mutants)-len(eligible),
        mutant_inconclusive=sum(r['status'] not in complete_statuses for r in eligible),
        mutation_detection_rate=ratio(kills, len(eligible)),
        elapsed_seconds=sum(r['elapsed_seconds'] for r in rows),
        limitation='Synthetic mutation smoke only. No official correctness, model efficacy, synthesis or RTL timing claim.')
    artifacts.json('evaluation.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixtures')
    parser.add_argument('output')
    parser.add_argument('--suite-root', help='Frozen suites from a model, indexed by fixture task id')
    parser.add_argument('--split', choices=['development', 'reserved', 'all'], default='development')
    parser.add_argument('--backend', choices=['native', 'vitis'], default='native')
    parser.add_argument('--compiler', default='g++')
    parser.add_argument('--include-dir', action='append', default=[])
    parser.add_argument('--config')
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--allow-execution', action='store_true')
    args = parser.parse_args()
    result = evaluate(args.fixtures, args.output, suite_root=args.suite_root, split=args.split,
                      backend=args.backend, compiler=args.compiler, include_dirs=args.include_dir,
                      runtime=load_config(args.config, allow_external=True) if args.config else None,
                      timeout=args.timeout, allow_execution=args.allow_execution)
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}, indent=2))
    raise SystemExit(0 if result['correct_passes'] == result['tasks'] and not result['mutant_inconclusive'] else 2)
