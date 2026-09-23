"""Preview an offline strategy using explicitly supplied, released feedback."""
import argparse
import json
from pathlib import Path

from agent.core.contracts import Diagnostic
from agent.repair_strategies.router import ROUTES, load_pack


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--category', required=True)
    parser.add_argument('--stage', default='csim', choices=('csim', 'synthesis'))
    parser.add_argument('--feedback', default='', help='Already-released diagnostic text')
    parser.add_argument('--feedback-file', type=Path, help='UTF-8 file of already-released feedback, not raw logs')
    parser.add_argument('--json', action='store_true', help='Include selection reason and hashes')
    args = parser.parse_args()
    if args.feedback_file is not None and args.feedback:
        parser.error('Use either --feedback or --feedback-file')
    feedback = args.feedback_file.read_text(encoding='utf-8') if args.feedback_file else args.feedback
    diagnostic = Diagnostic(args.category, args.stage, feedback, '', args.category in ROUTES)
    selection = load_pack().select(diagnostic)
    print(json.dumps(selection.snapshot(), ensure_ascii=False, indent=2) if args.json
          else selection.render() or 'No code-repair strategy: ' + selection.reason)


if __name__ == '__main__':
    main()

