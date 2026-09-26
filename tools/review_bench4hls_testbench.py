"""Run the finite-reference gate over one saved generation directory."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def bounded_integer(maximum):
    def parse(value):
        value = int(value)
        if not 1 <= value <= maximum:
            raise argparse.ArgumentTypeError('must be between 1 and ' + str(maximum))
        return value
    return parse

def main(argv=None):
    p = argparse.ArgumentParser(description='Exit 0 means finite reference agreement, not automatic acceptance.')
    p.add_argument('--problem', type=Path, required=True)
    p.add_argument('--top', default='TopModule')
    p.add_argument('--generation', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--oracle', type=Path)
    p.add_argument('--oracle-sha256')
    p.add_argument('--max-product-states', type=bounded_integer(65536), default=4096)
    p.add_argument('--max-transitions', type=bounded_integer(1_000_000), default=65536)
    a = p.parse_args(argv)
    if a.output.exists():
        p.error('Output already exists: ' + str(a.output))
    from agent.selftest.bench4hls_reference_gate import review_testbench
    report = review_testbench(a.problem.read_bytes(), a.top, a.generation, a.output, oracle_path=a.oracle, expected_oracle_sha256=a.oracle_sha256, max_product_states=a.max_product_states, max_transitions=a.max_transitions)
    print(json.dumps({'status': report['status'], 'report': str(a.output / 'report.json'), 'automatic_acceptance_allowed': False}))
    return 0 if report.get('status') == 'reference_checked' else 1
if __name__ == '__main__':
    raise SystemExit(main())
