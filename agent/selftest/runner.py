"""Run a frozen suite; compare observed values independently of process exit codes."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import time

from agent.artifacts.writer import Artifacts
from agent.core.contracts import Candidate, Material, TaskSpec, digest, json_digest
from evaluation.hls import run_process
from evaluation.validator import HLSValidator
from serve.inference import Failure
from .generate import load_suite
from .spec import SelftestError, integer, keys, parse_json, require


def observations(log, vectors, exit_ok):
    """Require exactly one ordered observation per vector and a consistent final summary."""
    cases, endings = [], []
    seen_end = False
    for line in log.splitlines():
        if line.startswith('SELFTEST_CASE '):
            require(not seen_end, 'Observation after summary')
            event = parse_json(line[len('SELFTEST_CASE '):])
            keys(event, {'case_id', 'actual'}, 'case observation')
            integer(event['case_id'], 0, len(vectors) - 1, 'case_id')
            integer(event['actual'], -(2**63), 2**63 - 1, 'actual')
            require(event['case_id'] == len(cases), 'Missing, duplicate or reordered case observation')
            cases.append(event)
        elif line.startswith('SELFTEST_END '):
            seen_end = True
            event = parse_json(line[len('SELFTEST_END '):])
            keys(event, {'count', 'failures'}, 'summary')
            integer(event['count'], 0, len(vectors), 'summary count')
            integer(event['failures'], 0, len(vectors), 'summary failures')
            endings.append(event)
    require(len(cases) == len(vectors) and len(endings) == 1, 'No complete test execution evidence')
    failures = [{**v, 'actual': c['actual']} for c, v in zip(cases, vectors) if c['actual'] != v['expected']]
    require(endings[0] == {'count': len(vectors), 'failures': len(failures)}, 'Inconsistent summary')
    # A failed tool with no mismatches is not a functional pass.
    require(not (not failures and not exit_ok), 'Tool failed despite matching observations')
    require(not (failures and exit_ok), 'Tool passed despite mismatches')
    return {'status': 'selftest_failed' if failures else 'selftest_passed',
            'executed_cases': len(cases), 'mismatch_count': len(failures),
            'first_failure': failures[0] if failures else None, 'failures': failures[:20]}


def _read_log(path):
    require(path.stat().st_size <= 32_000_000, 'Log too large to parse safely')
    return path.read_text(encoding='utf-8', errors='replace')


def run_suite(suite, source, output, *, backend='native', compiler='g++', include_dirs=(),
              runtime=None, timeout=120, allow_execution=False):
    artifacts = Artifacts(output)
    started = time.monotonic()
    result = dict(schema_version=1, status='running', backend=backend,
                  evidence_origin='self_generated_tests', semantic_correctness='unverified',
                  official_correctness='not_evaluated', executed_cases=0, mismatch_count=None,
                  first_failure=None, stages={}, review_required=True)
    artifacts.json('selftest_result.json', result)
    try:
        require(allow_execution, 'Compiling/running C++ needs explicit --allow-execution; use a disposable sandbox for untrusted code')
        require(type(timeout) in (int, float) and 0 < timeout <= 3600, 'timeout must be in (0,3600]')
        require(backend in {'native', 'vitis'}, 'Unknown backend')
        manifest, data, contract, vectors = load_suite(suite)
        result.update(suite_id=manifest['suite_id'], coverage=manifest['coverage'],
                      generation_origin=manifest['origin'])
        candidate_source = Path(source).read_bytes()
        require(0 < len(candidate_source) <= 1_000_000, 'Invalid candidate size')
        candidate_source.decode('utf-8')
        result['candidate_sha256'] = digest(candidate_source)
        # Per-run copies ensure testbench/oracle originals cannot be accidentally overwritten.
        inputs = artifacts.root / 'input'
        inputs.mkdir()
        for name in ('interface.h', 'testbench.cpp'):
            artifacts.bytes('input/' + name, data[name])
        artifacts.bytes('input/solution.cpp', candidate_source)
        artifacts.json('suite_snapshot.json', manifest)
        deadline = started + timeout
        if backend == 'native':
            result.update(_native(artifacts, inputs, compiler, include_dirs, vectors, deadline, result['stages']))
        else:
            require(runtime is not None, 'Vitis backend needs an explicit runtime config')
            artifacts.json('runtime.json', runtime)
            result.update(_vitis(artifacts, data, contract, candidate_source, vectors, runtime, deadline, result['stages']))
        for name, original in [('interface.h', data['interface.h']), ('testbench.cpp', data['testbench.cpp']), ('solution.cpp', candidate_source)]:
            require((inputs / name).read_bytes() == original, 'Execution changed frozen input: ' + name)
        require(load_suite(suite)[0]['suite_id'] == manifest['suite_id'], 'Original suite changed during execution')
    except (SelftestError, Failure, OSError, ValueError, TypeError, KeyError) as error:
        result.update(status='inconclusive', category=getattr(error, 'category', 'selftest_execution_error'), message=str(error))
    result['elapsed_seconds'] = round(time.monotonic() - started, 3)
    artifacts.json('selftest_result.json', result)
    return result


def _native(artifacts, inputs, compiler, include_dirs, vectors, deadline, stages):
    executable = shutil.which(str(compiler))
    require(executable is not None, 'C++ compiler not found: ' + str(compiler))
    executable = str(Path(executable).resolve())
    flags = ['-std=c++14', '-O0', '-I', str(inputs)]
    for directory in include_dirs:
        directory = Path(directory).resolve()
        require(directory.is_dir(), 'Include directory not found')
        flags.extend(['-I', str(directory)])
    # Reuse process-tree timeouts. This is process isolation, NOT an OS security sandbox.
    environment = os.environ.copy()
    for key in list(environment):
        if key.endswith(('_API_KEY', '_TOKEN')):
            environment.pop(key)
    def execute(name, command):
        remaining = deadline - time.monotonic()
        require(remaining > 0, 'Execution time budget exhausted')
        artifacts.json(name + '_command.json', command)
        execution = run_process(command, artifacts.root, environment, artifacts.root / (name + '.log'), remaining)
        stages[name] = execution
        return execution
    # Version and command provenance, not a claim of reproducibility across compilers.
    version = execute('compiler_version', [executable, '--version'])
    require(version['exit_code'] == 0 and not version['timed_out'], 'Compiler unavailable')
    for kind, filename in [('testbench', 'testbench.cpp'), ('candidate', 'solution.cpp')]:
        result = execute(kind + '_compile', [executable, *flags, '-c', str(inputs / filename), '-o', kind + '.o'])
        if result['timed_out']:
            return {'status': 'inconclusive', 'category': 'build_timeout'}
        if result['exit_code'] != 0:
            return {'status': kind + '_build_error', 'category': kind + '_compile_error'}
    binary = artifacts.root / ('selftest.exe' if os.name == 'nt' else 'selftest')
    result = execute('link', [executable, 'testbench.o', 'candidate.o', '-o', str(binary)])
    if result['timed_out'] or result['exit_code'] != 0:
        return {'status': 'inconclusive', 'category': 'link_error_or_timeout'}
    result = execute('run', [str(binary)])
    if result['timed_out']:
        return {'status': 'inconclusive', 'category': 'runtime_timeout'}
    require(result['exit_code'] in (0, 1), 'Abnormal process termination')
    return observations(_read_log(artifacts.root / 'run.log'), vectors, result['exit_code'] == 0)


def _vitis(artifacts, data, contract, source, vectors, runtime, deadline, stages):
    manifest = dict(id='selftest', problem_file='problem.txt', source_file='solution.cpp',
                    top_function=contract['top_function'], testbench_files=['testbench.cpp'],
                    design_files=[], support_files=['interface.h'], include_dirs=['.'],
                    model_visible=[], cxx_standard='c++14', feedback_policy='functional_diagnostics')
    task = TaskSpec(data['problem.txt'], manifest,
                    [Material(name, data[name]) for name in ('interface.h', 'testbench.cpp')])
    validator = HLSValidator(cpu_only=True)
    validator.preflight(task, runtime, artifacts.root)
    path = artifacts.bytes('candidates/000/source.cpp', source)
    candidate = Candidate(0, source.decode('utf-8'), path, None)
    validation = validator.check(candidate, task, runtime, 'csim', deadline)
    require(validation.source_sha256 == candidate.sha256 and validation.task_sha256 == task.fingerprint
            and validation.config_sha256 == json_digest(runtime['hls']), 'Vitis evidence fingerprint mismatch')
    artifacts.json('validation.json', asdict(validation))
    stages['csim'] = validation.outcome
    outcome = validation.outcome
    if outcome.get('timed_out'):
        return {'status': 'inconclusive', 'category': 'csim_timeout'}
    log = _read_log(artifacts.root / outcome['log'])
    if 'SELFTEST_CASE ' not in log:
        # Compiler provenance in mixed Vitis builds is not sufficiently reliable to blame the DUT.
        return {'status': 'inconclusive', 'category': 'vitis_build_or_environment_error',
                'tool_category': outcome.get('category')}
    return observations(log, vectors, outcome['status'] == 'passed')
