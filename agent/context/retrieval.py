"""Repair-only retrieval adapter. Query inputs are problem and released feedback."""
import json
import os
from pathlib import Path
import sys
import time

from agent.core.contracts import digest, json_digest
from agent.core.policy import rag_options
from evaluation.hls import run_process
from serve.inference import Failure, ROOT, write_json


def load_runtime(path=None):
    local = ROOT / 'rag/runtime.local.json'
    source = Path(path) if path else local if local.is_file() else ROOT / 'rag/runtime.json'
    source = (ROOT / source).resolve() if not source.is_absolute() else source.resolve()
    data = json.loads(source.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not {'registry', 'python', 'model'}.issubset(data):
        raise ValueError('RAG runtime requires registry, python, model')
    data.setdefault('reranker', None)
    for key in data:
        if data[key] is None and key != 'registry':
            continue
        if not isinstance(data[key], str) or not data[key].strip():
            raise ValueError('Invalid RAG runtime field: ' + key)
        value = Path(data[key])
        # abspath (not resolve) so a venv bin/python symlink is not collapsed to
        # the base interpreter, which would lose the venv site-packages.
        data[key] = os.path.abspath(ROOT / value) if not value.is_absolute() else os.path.abspath(value)
    data['python'] = data['python'] or sys.executable
    return source, data


def make_query(problem, feedback, max_chars):
    # Reserve space for both inputs; long problem statements cannot erase feedback.
    prefix, separator = 'Vitis HLS task:\n', '\nReleased diagnostic:\n'
    room = max_chars - len(prefix) - len(separator)
    feedback_size = min(len(feedback), room // 2)
    problem_size = min(len(problem), room - feedback_size)
    feedback_size = min(len(feedback), room - problem_size)
    query = prefix + problem[:problem_size] + separator + feedback[:feedback_size]
    return query, {'problem_trimmed': problem_size < len(problem),
                   'feedback_trimmed': feedback_size < len(feedback),
                   'problem_sha256': digest(problem.encode('utf-8')),
                   'released_feedback_sha256': digest(feedback.encode('utf-8'))}


class Retrieval:
    def __init__(self, policy, runtime_path=None):
        self.options = rag_options(policy)
        self.enabled = self.options['rag_enabled']
        self.snapshot = {'enabled': False}
        if not self.enabled:
            self.sha256 = json_digest(self.snapshot)
            return  # No registry, corpus, model or runtime reads when disabled.
        try:
            from rag.release import load_release, verify_release
            self.runtime_path, self.runtime = load_runtime(runtime_path)
            self.release = load_release(self.runtime['registry'])
            verify_release(self.release)
            if self.options['rag_mode'] == 'hybrid' and not self.runtime['model']:
                raise ValueError('Hybrid RAG requires an explicit local model path')
            if self.options['rag_reranker'] != 'none' and not self.runtime.get('reranker'):
                raise ValueError('RAG reranker is enabled but no local reranker path was supplied')
            self.snapshot = dict(enabled=True, options=self.options, runtime=self.runtime, release=self.release)
            self.sha256 = json_digest(self.snapshot)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise Failure('rag_configuration_error', str(error)) from error

    def select(self, task, diagnostic, directory, deadline, validation_reserve):
        if not self.enabled or diagnostic is None:
            return None
        query, source = make_query(task.problem.decode('utf-8'), diagnostic.feedback,
                                   self.options['rag_query_max_chars'])
        feedback_policy = task.manifest['feedback_policy'] if task.manifest else None
        plan = {'strategy': 'legacy', 'skip_reason': None}
        if self.options['rag_strategy'] == 'diagnostic_v1':
            from rag.query import query_plan
            query, plan = query_plan(task.problem.decode('utf-8'), diagnostic.feedback,
                                     diagnostic.category, feedback_policy,
                                     self.options['rag_query_max_chars'])
            source.update({k: plan[k] for k in ('problem_trimmed', 'feedback_trimmed', 'feedback_cleaned')})
        evidence = dict(status='running', query=query, query_source=source,
                        feedback_policy=feedback_policy, query_plan=plan,
                        diagnostic_annotation=plan.get('annotation'),
                        diagnostic_fingerprint=diagnostic.fingerprint, mode=self.options['rag_mode'],
                        profile=self.options['rag_profile'], reranker=self.options['rag_reranker'],
                        rag_config_sha256=self.sha256, release_id=self.release['release_id'],
                        corpus_sha256=self.release['records_sha256'],
                        index_fingerprint=self.release['index_fingerprint'],
                        retrieved_ids=[], injected_ids=[], injected_bytes=0)
        directory = Path(directory)
        evidence_path = directory / 'retrieval.json'
        write_json(evidence_path, evidence)
        started = time.monotonic()
        try:
            if plan['skip_reason']:
                evidence.update(status='skipped', skip_reason=plan['skip_reason'], hits=[])
                return evidence
            timeout = min(self.options['rag_timeout_seconds'], deadline - started - validation_reserve)
            if timeout <= 0:
                raise Failure('rag_insufficient_budget', 'No retrieval time available after validation reserve')
            job = dict(query=query, options=self.options, release=self.release, model=self.runtime['model'],
                       reranker=self.runtime.get('reranker'),
                       query_plan=plan)
            job_path = directory / 'retrieval_input.json'
            output = directory / 'retrieval_output.json'
            write_json(job_path, job)
            environment = os.environ.copy()
            environment.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', HF_HUB_OFFLINE='1',
                               TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
            execution = run_process([self.runtime['python'], '-X', 'utf8', '-B', '-m', 'rag.worker',
                                     str(job_path), str(output)], ROOT, environment,
                                    directory / 'retrieval.log', timeout)
            evidence['execution'] = execution
            if execution['timed_out']:
                raise Failure('rag_timeout', 'Retrieval worker exceeded its time budget; no silent fallback')
            result = json.loads(output.read_text(encoding='utf-8')) if output.is_file() else {}
            if execution['exit_code'] != 0 or result.get('status') != 'completed':
                raise Failure('rag_retrieval_error', result.get('error', 'Retrieval worker did not complete'))
            if result.get('corpus_sha256') != self.release['records_sha256'] or result.get('query') != query:
                raise Failure('rag_evidence_mismatch', 'Retrieval output differs from request/release')
            evidence.update(status='retrieved', retrieved_ids=[h['record']['id'] for h in result['hits']],
                            hits=result['hits'], retrieval_context_bytes=result['context_bytes'],
                            selection_audit=result.get('selection_audit', []),
                            top_k_requested=result.get('top_k_requested'),
                            eligible_count=result.get('eligible_count'),
                            rejected_count=result.get('rejected_count'),
                            injection_policy=result.get('injection_policy'),
                            no_reference_reason=result.get('no_reference_reason'))
            return evidence
        except (Failure, OSError, ValueError, KeyError, TypeError) as error:
            evidence.update(status='failed', category=getattr(error, 'category', 'rag_retrieval_error'), error=str(error))
            if isinstance(error, Failure):
                raise
            raise Failure('rag_retrieval_error', str(error)) from error
        finally:
            evidence['elapsed_seconds'] = round(time.monotonic() - started, 4)
            write_json(evidence_path, evidence)


def input_artifacts(policy, runtime_path=None):
    """Files to freeze across evaluation retries and paired runs; no reads when off."""
    if not rag_options(policy)['rag_enabled']:
        return []
    from rag.release import load_release, release_files
    source, runtime = load_runtime(runtime_path)
    release = load_release(runtime['registry'])
    paths = [source, Path(runtime['registry']), *release_files(release)]
    if rag_options(policy)['rag_mode'] == 'hybrid' and runtime['model']:
        model_root = Path(runtime['model']).resolve()
        manifest_path = model_root / 'rag-model-manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        paths.append(manifest_path)
        for name in manifest['files']:
            target = (model_root / name).resolve()
            if not target.is_relative_to(model_root):
                raise ValueError('Model manifest path escapes model directory')
            paths.append(target)
    if rag_options(policy)['rag_reranker'] != 'none' and runtime.get('reranker'):
        reranker_root = Path(runtime['reranker']).resolve()
        manifest_path = reranker_root / 'rag-reranker-manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        paths.append(manifest_path)
        for name in manifest['files']:
            target = (reranker_root / name).resolve()
            if not target.is_relative_to(reranker_root):
                raise ValueError('Reranker manifest path escapes model directory')
            paths.append(target)
    return paths
