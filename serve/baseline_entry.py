'''Problem-only generation: no agent, skills, tokenization, tools or retry.'''
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from serve.inference import Failure, generate, load_config, write_json


def digest(data):
    return hashlib.sha256(data).hexdigest()


def config_digest(config):
    return digest(json.dumps(config, sort_keys=True, separators=(',', ':')).encode())


def run(problem_path, output, config, run_id):
    problem_bytes = Path(problem_path).read_bytes()
    problem = problem_bytes.decode('utf-8')
    if not problem.strip():
        raise ValueError('Empty problem')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / 'problem.txt').write_bytes(problem_bytes)
    write_json(output / 'config.json', config)
    model = config['model']
    request = dict(model=model['name'], messages=[dict(role='user', content=problem)],
                   max_tokens=model['max_tokens'], temperature=model['temperature'],
                   chat_template_kwargs=dict(enable_thinking=model['enable_thinking']), stream=False)
    write_json(output / 'request.json', request)
    result = dict(schema_version=1, run_id=run_id, branch='baseline', status='running',
                  started_at=datetime.now(timezone.utc).isoformat(), problem_sha256=digest(problem_bytes),
                  config_sha256=config_digest(config), model=model, generation_requests=0,
                  retries=0, tool_calls=0, agent_used=False, skills_used=False, validation='not_run',
                  source_extraction='not_run', paired_run=False)
    write_json(output / 'result.json', result)
    started = time.monotonic()
    code = 1
    try:
        generate(problem, config, output / 'response.txt')
        raw = (output / 'response.txt').read_bytes().decode('utf-8')
        match = re.fullmatch(r'```(?:cpp|c\+\+|c|cc)?\s*\n(.*?)\n```', raw.strip(), re.DOTALL | re.I)
        if '```' in raw and (not match or '```' in match.group(1)):
            raise Failure('response_format_error', 'Ambiguous Markdown; raw response preserved without repair')
        source = match.group(1) + '\n' if match else raw
        (output / 'candidate.cpp').write_bytes(source.encode('utf-8'))
        result.update(status='generated', source='candidate.cpp', source_sha256=digest((output/'candidate.cpp').read_bytes()),
                      source_extraction='single_outer_fence_removed' if match else 'verbatim')
        code = 0
    except Failure as error:
        result.update(status='failed', category=error.category, message=str(error))
    except Exception as error:
        result.update(status='failed', category=type(error).__name__, message=str(error))
    finally:
        meta_path = output / 'response.txt.meta.json'
        metadata = json.loads(meta_path.read_text(encoding='utf-8')) if meta_path.is_file() else {}
        result.update(generation_requests=metadata.get('requests', 0), usage=metadata.get('usage'),
                      finish_reason=metadata.get('finish_reason'), elapsed_seconds=round(time.monotonic()-started, 3),
                      finished_at=datetime.now(timezone.utc).isoformat())
        write_json(output / 'result.json', result)
    return code, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('problem', help='Problem text passed verbatim')
    parser.add_argument('output', help='New output directory')
    parser.add_argument('--config', default='serve/runtime.json')
    parser.add_argument('--run-id')
    args = parser.parse_args()
    try:
        code, result = run(args.problem, args.output, load_config(args.config), args.run_id or uuid.uuid4().hex)
        print(json.dumps(result, ensure_ascii=False))
        return code
    except (Failure, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
