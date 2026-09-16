"""Dataset-independent outer evaluation scope, inherited by all child processes."""
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

from agent.core.policy import load_policy
from evaluation.task_io import load_manifest
from local_eval.retry import classify, load_settings, run_session
from serve.inference import Failure, ROOT, load_config, write_json

SCOPE_ENV = 'ZCOMP_DEVELOPMENT_ATTEMPT'


def scope():
    value = os.environ.get(SCOPE_ENV)
    if not value:
        return None
    path = Path(value).resolve()
    if not (path / 'scope.json').is_file():
        raise Failure('development_scope_error', 'Missing development attempt descriptor: ' + str(path))
    return path


def output_path(path):
    """Redirect every output of a round into that round, including batch children."""
    attempt = scope()
    if attempt is None:
        return path
    path = Path(path).resolve()
    if path.is_relative_to(attempt):
        return path
    descriptor = json.loads((attempt / 'scope.json').read_text(encoding='utf-8'))
    requested = descriptor.get('requested_output')
    if requested and path == Path(requested):
        target = attempt / ('response.txt' if descriptor['output_is_file'] else 'result')
    elif path.is_relative_to(ROOT / 'output'):
        target = attempt / 'artifacts' / path.relative_to(ROOT / 'output')
    else:
        key = hashlib.sha256(str(path).encode()).hexdigest()[:12]
        target = attempt / 'artifacts' / (key + '_' + path.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def before_request():
    attempt = scope()
    if attempt and any((attempt / 'network_failures').glob('*.json')):
        raise Failure('local_evaluation_aborted', 'Network failure invalidated this entire evaluation round')


def observe_request(path, metadata):
    attempt = scope()
    if attempt is None:
        return
    descriptor = json.loads((attempt / 'scope.json').read_text(encoding='utf-8'))
    evidence = str(Path(path).resolve())
    key = hashlib.sha256(evidence.encode()).hexdigest()
    # One file per request path; overwrite sending with final metadata atomically.
    # Do not copy prompts, credentials or model configuration into the journal.
    record = {k: metadata[k] for k in ('status', 'category', 'http_status', 'requests',
              'elapsed_seconds', 'request_outcome_unknown') if k in metadata}
    record['evidence'] = evidence
    write_json(attempt / 'requests' / (key + '.json'), record)
    reason = classify(metadata, descriptor['settings'])
    if reason:
        write_json(attempt / 'network_failures' / (key + '.json'), dict(record, reason=reason))


def input_files(args):
    """Track declared evaluation inputs without reading result/code failure corpora."""
    files = set()
    for field in ('problem', 'source', 'config', 'policy', 'selection', 'agent_entry'):
        value = getattr(args, field, None)
        if value:
            candidate = Path(value).resolve()
            if candidate.is_file():
                files.add(candidate)
    manifests = []
    for field in ('manifest', 'task_manifest'):
        value = getattr(args, field, None)
        if value:
            manifests.append(Path(value).resolve())
    dataset = getattr(args, 'dataset', None)
    if dataset:
        dataset = Path(dataset).resolve()
        manifests += sorted(dataset.rglob('task.json'))
        if (dataset / 'selection.json').is_file():
            files.add(dataset / 'selection.json')
    for path in manifests:
        _, names = load_manifest(path)
        files.add(path)
        files.update(path.parent / name for name in names)
    # Legacy entries load the default policy internally.
    files.add(ROOT / 'agent/config/policy.json')
    skills = Path(getattr(args, 'skills_dir', None) or ROOT / 'skill/rules')
    files.update(skills.glob('*.json'))
    return {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}


def evaluate(args, run_once, *, output=None, output_is_file=False):
    if scope() is not None:
        # Batch/pair children participate in their parent's round. They cannot
        # hide a network fault by creating an independent retry loop.
        try:
            before_request()
        except Failure as error:
            print(error.category + ': ' + str(error), flush=True)
            return 1
        return run_once(args)

    requested = Path(output).resolve() if output is not None else None
    if requested is not None and (requested.exists() or (output_is_file and Path(str(requested) + '.meta.json').exists())):
        raise FileExistsError('Refusing to overwrite evaluation output: ' + str(requested))
    session = (Path(str(requested) + '.evaluation') if output_is_file else requested) if requested else (
        ROOT / 'output/local_eval' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:8]))
    settings = load_settings()
    frozen_args = copy.deepcopy(args)
    # Resolve environment overrides once, then pass the same configuration to
    # every branch and every restart. Never store credentials in this snapshot.
    runtime = load_config(getattr(args, 'config', None))
    fingerprints = input_files(args)
    session.mkdir(parents=True, exist_ok=False)
    write_json(session / 'runtime.json', runtime)
    if hasattr(frozen_args, 'config'):
        frozen_args.config = str(session / 'runtime.json')
    if hasattr(frozen_args, 'policy'):
        policy = load_policy(getattr(args, 'policy', None))
        write_json(session / 'policy.json', policy)
        frozen_args.policy = str(session / 'policy.json')
    write_json(session / 'inputs.json', fingerprints)
    snapshot_hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (session / 'runtime.json', session / 'policy.json') if p.exists()}

    def verify():
        if input_files(args) != fingerprints:
            raise Failure('evaluation_inputs_changed', 'Evaluation inputs changed; start a new evaluation')
        for path, expected in snapshot_hashes.items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
                raise Failure('evaluation_inputs_changed', 'Frozen configuration changed')

    def once(attempt):
        for name in ('requests', 'network_failures'):
            (attempt / name).mkdir()
        write_json(attempt / 'scope.json', dict(settings=settings, requested_output=str(requested) if requested else None,
                                               output_is_file=output_is_file))
        os.environ[SCOPE_ENV] = str(attempt)
        overrides = {key: os.environ.pop(key) for key in ('LLM_BASE_URL', 'LLM_MODEL', 'LLM_MAX_TOKENS') if key in os.environ}
        try:
            code = run_once(copy.deepcopy(frozen_args))
            requests = [json.loads(p.read_text(encoding='utf-8')) for p in (attempt / 'requests').glob('*.json')]
            failures = [json.loads(p.read_text(encoding='utf-8')) for p in (attempt / 'network_failures').glob('*.json')]
            return dict(exit_code=code, network_failures=failures,
                        api_requests_recorded=sum(r.get('requests', 0) for r in requests),
                        request_outcomes_unknown=sum(r.get('status') == 'sending' or r.get('request_outcome_unknown', False)
                                                     or r.get('category') == 'api_network_or_timeout' and bool(r.get('requests'))
                                                     for r in requests),
                        output=str(output_path(requested)) if requested else str(attempt / 'artifacts'))
        finally:
            os.environ.pop(SCOPE_ENV, None)
            os.environ.update(overrides)

    print('[local_eval] Mandatory development evaluation: ' + str(session), flush=True)
    code, summary = run_session(session, settings, once, verify=verify)
    # Legacy generation CLI promises a file. Export only the accepted round;
    # retain every original request and response under the session directory.
    if output_is_file and summary['valid_attempt'] and code == 0:
        response = session / summary['valid_attempt'] / 'response.txt'
        requested.parent.mkdir(parents=True, exist_ok=True)
        with requested.open('xb') as target:
            target.write(response.read_bytes())
        metadata = Path(str(response) + '.meta.json')
        if metadata.exists():
            with Path(str(requested) + '.meta.json').open('xb') as target:
                target.write(metadata.read_bytes())
    print(json.dumps(dict(local_eval=True, status=summary['status'], valid_attempt=summary['valid_attempt'],
                          session=str(session / 'session.json')), ensure_ascii=False), flush=True)
    return code
