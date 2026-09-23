"""Standalone CLI. Never imports or changes the main controller."""
import argparse
import json
from pathlib import Path

from serve.inference import Failure, load_config
from .generate import generate_suite, load_suite
from .audit import DEFAULT_AUDIT_MAX_CASES
from .runner import run_suite
from .spec import PublicTask, SelftestError, parse_json


def main(argv=None):
    parser = argparse.ArgumentParser(description='Independent scalar HLS self-test prototype')
    sub = parser.add_subparsers(dest='command', required=True)
    generate = sub.add_parser('generate', help='Generate from public problem + interface only')
    generate.add_argument('problem')
    generate.add_argument('interface')
    generate.add_argument('output')
    mode = generate.add_mutually_exclusive_group(required=True)
    mode.add_argument('--response-file', help='Replay a recorded contract/test_plan bundle; no model call')
    mode.add_argument('--config', help='Explicit model runtime; makes up to two model requests')
    generate.add_argument('--max-cases', type=int, default=4096)
    generate.add_argument('--timeout', type=float, default=180)
    generate.add_argument('--planning-policy', choices=['strict', 'bounded-v2'], default='strict')
    audit = sub.add_parser('audit', help='Audit a frozen oracle without candidate access or model calls')
    audit.add_argument('suite')
    audit.add_argument('output')
    audit.add_argument('--binding', help='Explicit suite-bound rule selection with public evidence')
    audit.add_argument('--anchors', help='Explicit suite-bound anchor values with public evidence')
    audit.add_argument('--max-cases', type=int, default=DEFAULT_AUDIT_MAX_CASES,
                       help='Primary-input budget (default 65536 covers supported 1..16-bit rule domains); explicit smaller budgets remain sampling')
    audit.add_argument('--seed', type=int, default=20260923)
    check = sub.add_parser('inspect', help='Verify suite hashes, schema and deterministic rendering')
    check.add_argument('suite')
    run = sub.add_parser('run', help='Run a frozen suite against one candidate; never repair the test')
    run.add_argument('suite')
    run.add_argument('source')
    run.add_argument('output')
    run.add_argument('--backend', choices=['native', 'vitis'], default='native')
    run.add_argument('--compiler', default='g++')
    run.add_argument('--include-dir', action='append', default=[])
    run.add_argument('--config')
    run.add_argument('--timeout', type=float, default=120)
    run.add_argument('--allow-execution', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'generate':
            task = PublicTask.load(args.problem, args.interface)
            options = dict(max_cases=args.max_cases, timeout=args.timeout, planning_policy=args.planning_policy)
            if args.response_file:
                options['response'] = parse_json(Path(args.response_file).read_text(encoding='utf-8'))
            else:
                from serve.agent_model import ModelClient
                options.update(model=ModelClient(), runtime=load_config(args.config, allow_external=True))
            result = generate_suite(task, args.output, **options)
            code = 0 if result['status'] == 'generated_unreviewed' else 2
        elif args.command == 'inspect':
            result = load_suite(args.suite)[0]
            code = 0
        elif args.command == 'audit':
            from .audit import audit_suite
            result = audit_suite(args.suite, args.output, binding=args.binding, anchor_file=args.anchors,
                                 max_cases=args.max_cases, seed=args.seed)
            code = 0 if result['status'] == 'supported' else 1 if result['status'] == 'conflict' else 2
        else:
            runtime = load_config(args.config, allow_external=True) if args.config else None
            result = run_suite(args.suite, args.source, args.output, backend=args.backend,
                               compiler=args.compiler, include_dirs=args.include_dir, runtime=runtime,
                               timeout=args.timeout, allow_execution=args.allow_execution)
            code = 0 if result['status'] == 'selftest_passed' else 1 if result['status'] == 'selftest_failed' else 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return code
    except (SelftestError, Failure, OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({'status': 'failed', 'message': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
