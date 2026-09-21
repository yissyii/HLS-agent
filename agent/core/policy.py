"""Frozen policy parameters and bounded repair decisions."""
import json
import math
from pathlib import Path

from serve.inference import Failure

DEFAULT_PATH = Path(__file__).resolve().parents[1] / 'config/policy.json'

RAG_DEFAULTS = dict(rag_enabled=False, rag_mode='hybrid', rag_top_k=2, rag_recall_k=20,
                    rag_max_bytes=2400, rag_query_max_chars=1600, rag_timeout_seconds=30,
                    rag_strategy='diagnostic_v1', rag_profile='fix_first',
                    rag_reranker='none', rag_rerank_k=20)


def rag_options(policy):
    return {name: policy.get(name, default) for name, default in RAG_DEFAULTS.items()}


def load_policy(path=None):
    value = json.loads(Path(path or DEFAULT_PATH).read_text(encoding='utf-8'))
    validate_policy(value)
    return value


def validate_policy(value):
    integer_limits = {'schema_version': (1, 1), 'max_repairs': (0, 5),
                      'stagnation_limit': (1, 10), 'max_skills': (0, 10),
                      'context_safety_tokens': (128, 8192), 'diagnostic_max_chars': (128, 12000)}
    expected = set(integer_limits) | {'validation_reserve_seconds', 'cleanup_reserve_seconds', 'skills_enabled'}
    if not expected.issubset(value) or set(value) - expected - set(RAG_DEFAULTS):
        raise Failure('policy_error', 'Unexpected or missing policy fields')
    for name, (minimum, maximum) in integer_limits.items():
        if type(value[name]) is not int or not minimum <= value[name] <= maximum:
            raise Failure('policy_error', 'Invalid policy field: ' + name)
    for name in ('validation_reserve_seconds', 'cleanup_reserve_seconds'):
        if type(value[name]) not in (int, float) or not math.isfinite(value[name]) or value[name] < 0:
            raise Failure('policy_error', 'Invalid policy field: ' + name)
    if type(value['skills_enabled']) is not bool:
        raise Failure('policy_error', 'skills_enabled must be boolean')
    rag = rag_options(value)
    if rag['rag_strategy'] not in ('legacy', 'diagnostic_v1'):
        raise Failure('policy_error', 'Invalid RAG strategy')
    if type(rag['rag_enabled']) is not bool or rag['rag_mode'] not in {'bm25', 'hybrid'}:
        raise Failure('policy_error', 'Invalid RAG mode or enable flag')
    for key, low, high in (('rag_top_k', 1, 10), ('rag_recall_k', 1, 100),
                           ('rag_max_bytes', 1, 20000), ('rag_query_max_chars', 128, 4000)):
        if type(rag[key]) is not int or not low <= rag[key] <= high:
            raise Failure('policy_error', 'Invalid RAG limit: ' + key)
    if rag['rag_recall_k'] < rag['rag_top_k']:
        raise Failure('policy_error', 'RAG recall count must cover top_k')
    if rag['rag_profile'] not in {'fix_first', 'general_only', 'all'}:
        raise Failure('policy_error', 'Invalid RAG corpus profile')
    if rag['rag_reranker'] not in {'none', 'qwen3-reranker-0.6b'}:
        raise Failure('policy_error', 'Invalid RAG reranker')
    if type(rag['rag_rerank_k']) is not int or not rag['rag_top_k'] <= rag['rag_rerank_k'] <= 100:
        raise Failure('policy_error', 'Invalid RAG rerank count')
    timeout = rag['rag_timeout_seconds']
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise Failure('policy_error', 'Invalid RAG timeout')


def decide(policy, diagnostic, repairs_used, budget, stagnant):
    if not diagnostic.repairable:
        return diagnostic.category
    if repairs_used >= policy['max_repairs']:
        return 'repair_budget_exhausted'
    if stagnant >= policy['stagnation_limit']:
        return 'stagnation'
    if budget.remaining() <= policy['validation_reserve_seconds']:
        return 'insufficient_validation_budget'
    return 'repair'
