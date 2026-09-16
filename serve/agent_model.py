"""Bounded model worker; uses the common transport without invoking baseline."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.hls import run_process
from serve.inference import Failure, generate, load_config, request_payload, write_json


class ModelClient:
    def generate(self, prompt, runtime, directory, deadline):
        directory = Path(directory)
        (directory / 'prompt.txt').write_bytes(prompt.text.encode('utf-8'))
        write_json(directory / 'request.json', request_payload(prompt.text, runtime))
        write_json(directory / 'context.json', {'provenance': prompt.provenance, **prompt.context, 'skills': prompt.skills})
        write_json(directory / 'config.json', runtime)
        remaining = min(runtime['model']['timeout_seconds'], deadline - time.monotonic())
        if remaining <= 0:
            raise Failure('total_timeout', 'No time remaining for generation')
        values = os.environ.copy()
        values.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
        execution = run_process([sys.executable, '-B', str(Path(__file__).resolve()),
                                 str(directory), '--timeout', str(remaining)], directory, values,
                                directory / 'generation.log', remaining)
        metadata_path = directory / 'response.txt.meta.json'
        metadata = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.is_file() else {}
        result = {**metadata, 'execution': execution}
        if execution['timed_out']:
            result.update(status='failed', category='generation_timeout')
        elif execution['exit_code'] != 0:
            result.update(status='failed', category=metadata.get('category', 'generation_error'))
        result['request_outcome_unknown'] = (not metadata or metadata.get('status') == 'sending'
                                             or bool(metadata.get('requests')) and metadata.get('category') == 'api_network_or_timeout')
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory')
    parser.add_argument('--timeout', type=float, required=True)
    args = parser.parse_args()
    directory = Path(args.directory)
    try:
        runtime = load_config(directory / 'config.json', frozen=True, allow_external=True)
        generate((directory / 'prompt.txt').read_bytes().decode('utf-8'), runtime,
                 directory / 'response.txt', timeout=args.timeout)
        return 0
    except Failure as error:
        print(error.category + ': ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
