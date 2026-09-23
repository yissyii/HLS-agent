"""Two-stage generation and immutable suite publication, separate from solve()."""
import time
from pathlib import Path

from agent.artifacts.writer import Artifacts
from agent.core.contracts import PromptBundle, digest, json_digest
from serve.inference import Failure
from . import VERSION
from .oracle import expand
from .render import canonical, render_testbench
from .sampling import POLICY, plan_bounded
from .spec import PublicTask, SelftestError, keys, parse_json, require, validate_contract


PROMPTS = Path(__file__).parent / 'prompts'
SUITE_FILES = ('problem.txt', 'interface.h', 'contract.json', 'test_plan.json', 'vectors.json', 'testbench.cpp')


def make_prompt(stage, task, contract=None, max_cases=4096):
    system = (PROMPTS / (stage + '.md')).read_text(encoding='utf-8')
    payload = {'public_problem': task.problem, 'public_interface': task.interface, 'max_cases': max_cases}
    if contract is not None:
        payload['contract'] = contract
    return PromptBundle(canonical(payload), [{'kind': 'public_inputs', **task.snapshot()}],
                        {'selftest_version': VERSION, 'stage': stage, 'candidate_visible': False}, system=system)


def generate_suite(task, output, *, response=None, model=None, runtime=None, max_cases=4096, timeout=180, planning_policy='strict'):
    """response is a recorded contract/plan bundle for offline review/replay, not a model benchmark."""
    artifacts = Artifacts(output)
    started = time.monotonic()
    result = dict(schema_version=1, module_version=VERSION, status='running',
                  origin='recorded_response' if response is not None else 'model',
                  generation_requests=0, semantic_correctness='unverified', review_required=True,
                  candidate_visible=False, official_correctness='not_evaluated')
    artifacts.json('generation_result.json', result)
    try:
        task.validate()
        require(planning_policy in {'strict', POLICY}, 'Unknown planning policy')
        require(type(timeout) in (float, int) and 0 < timeout <= 3600, 'timeout must be in (0,3600]')
        require((response is not None) != (model is not None), 'Choose recorded response OR live model')
        artifacts.bytes('problem.txt', task.problem.encode('utf-8'))
        artifacts.bytes('interface.h', task.interface.encode('utf-8'))
        if response is not None:
            keys(response, {'contract', 'test_plan'}, 'response bundle')
            artifacts.json('recorded_response.json', response)
        else:
            require(runtime is not None, 'Live generation requires explicit runtime')
            artifacts.json('runtime.json', runtime)
        deadline = started + timeout
        def get(stage, contract=None):
            prompt = make_prompt(stage, task, contract, max_cases)
            directory = artifacts.root / ('generation_' + stage)
            directory.mkdir()
            artifacts.bytes(f'generation_{stage}/prompt.txt', prompt.text.encode('utf-8'))
            artifacts.bytes(f'generation_{stage}/system_prompt.txt', prompt.system.encode('utf-8'))
            artifacts.json(f'generation_{stage}/context.json', prompt.context)
            if response is not None:
                value = response['contract' if stage == 'contract' else 'test_plan']
                artifacts.json(f'generation_{stage}/response.json', value)
                return value
            remaining_tokens = runtime['model']['context_tokens'] - runtime['model']['max_tokens']
            require(len((prompt.text + prompt.system).encode('utf-8')) + 512 <= remaining_tokens,
                    'Conservative context budget exceeded; no input truncation')
            require(time.monotonic() < deadline, 'Generation time budget exhausted')
            result['generation_requests'] += 1
            artifacts.json('generation_result.json', result)
            metadata = model.generate(prompt, runtime, directory, deadline)
            artifacts.json(f'generation_{stage}/metadata.json', metadata)
            if metadata.get('status') != 'passed':
                raise Failure(metadata.get('category', 'generation_error'), 'Self-test generation did not complete')
            return parse_json((directory / 'response.txt').read_text(encoding='utf-8'))
        contract = validate_contract(get('contract'), task)
        artifacts.json('contract.json', contract)
        if contract['uncertainties']:
            result.update(status='blocked_specification', uncertainties=contract['uncertainties'])
        else:
            plan = get('plan', contract)
            suite_files = SUITE_FILES
            if planning_policy == POLICY:
                artifacts.json('requested_plan.json', plan)
                plan, planning = plan_bounded(plan, contract, max_cases)
                artifacts.json('planning.json', planning)
                suite_files += ('requested_plan.json', 'planning.json')
            vectors, coverage = expand(plan, contract, max_cases)
            artifacts.json('test_plan.json', plan)
            artifacts.json('vectors.json', vectors)
            artifacts.bytes('testbench.cpp', render_testbench(contract, vectors).encode('utf-8'))
            hashes = {name: digest((artifacts.root / name).read_bytes()) for name in suite_files}
            manifest = dict(schema_version=2 if planning_policy == POLICY else 1, module_version=VERSION, files=hashes,
                            public_inputs=task.snapshot(), max_cases=max_cases,
                            coverage=coverage, origin=result['origin'],
                            prompt_sha256={s: digest((artifacts.root / f'generation_{s}/system_prompt.txt').read_bytes()) for s in ('contract', 'plan')})
            manifest['suite_id'] = json_digest(manifest)
            artifacts.json('suite.json', manifest)
            result.update(status='generated_unreviewed', suite_id=manifest['suite_id'], coverage=coverage)
    except (SelftestError, Failure, OSError, ValueError, TypeError, KeyError) as error:
        result.update(status='failed', category=getattr(error, 'category', 'selftest_input_or_generation_error'), message=str(error))
    result['elapsed_seconds'] = round(time.monotonic() - started, 3)
    artifacts.json('generation_result.json', result)
    return result


def load_suite(directory):
    root = Path(directory).resolve()
    manifest = parse_json((root / 'suite.json').read_text(encoding='utf-8'))
    keys(manifest, {'schema_version', 'module_version', 'files', 'public_inputs', 'max_cases', 'coverage', 'origin', 'prompt_sha256', 'suite_id'}, 'suite')
    require(type(manifest['schema_version']) is int and manifest['schema_version'] in (1, 2)
            and manifest['module_version'] in ('0.1.0', VERSION), 'Unsupported suite version')
    require(manifest['suite_id'] == json_digest({k: v for k, v in manifest.items() if k != 'suite_id'}), 'Suite manifest changed')
    suite_files = SUITE_FILES + (('requested_plan.json', 'planning.json') if manifest['schema_version'] == 2 else ())
    keys(manifest['files'], suite_files, 'suite files')
    data = {}
    for name in suite_files:
        path = (root / name).resolve()
        require(path.is_relative_to(root), 'Suite file escapes root')
        data[name] = path.read_bytes()
        require(digest(data[name]) == manifest['files'][name], 'Frozen suite changed: ' + name)
    task = PublicTask(data['problem.txt'].decode('utf-8'), data['interface.h'].decode('utf-8'))
    require(task.snapshot() == manifest['public_inputs'], 'Public input fingerprint mismatch')
    contract = validate_contract(parse_json(data['contract.json'].decode('utf-8')), task)
    require(not contract['uncertainties'], 'Cannot run an ambiguous specification')
    plan = parse_json(data['test_plan.json'].decode('utf-8'), max_bytes=32_000_000)
    if manifest['schema_version'] == 2:
        requested = parse_json(data['requested_plan.json'].decode('utf-8'))
        effective, planning = plan_bounded(requested, contract, manifest['max_cases'])
        require(effective == plan, 'Effective plan differs from bounded policy')
        require(planning == parse_json(data['planning.json'].decode('utf-8'), max_bytes=32_000_000),
                'Planning trace differs from bounded policy')
    vectors, coverage = expand(plan, contract, manifest['max_cases'])
    require(vectors == parse_json(data['vectors.json'].decode('utf-8'), max_bytes=32_000_000), 'Vectors differ from plan')
    require(render_testbench(contract, vectors).encode('utf-8') == data['testbench.cpp'], 'Harness differs from trusted renderer')
    require(coverage == manifest['coverage'], 'Coverage metadata mismatch')
    return manifest, data, contract, vectors
