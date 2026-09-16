"""Frozen policy parameters and bounded repair decisions."""
import json
import math
from pathlib import Path

from serve.inference import Failure

DEFAULT_PATH = Path(__file__).resolve().parents[1] / 'config/policy.json'


def load_policy(path=None):
    value = json.loads(Path(path or DEFAULT_PATH).read_text(encoding='utf-8'))
    validate_policy(value)
    return value


def validate_policy(value):
    integer_limits = {'schema_version': (1, 1), 'max_repairs': (0, 5),
                      'stagnation_limit': (1, 10), 'max_skills': (0, 10),
                      'context_safety_tokens': (128, 8192), 'diagnostic_max_chars': (128, 12000)}
    expected = set(integer_limits) | {'validation_reserve_seconds', 'cleanup_reserve_seconds', 'skills_enabled'}
    if set(value) != expected:
        raise Failure('policy_error', 'Unexpected or missing policy fields')
    for name, (minimum, maximum) in integer_limits.items():
        if type(value[name]) is not int or not minimum <= value[name] <= maximum:
            raise Failure('policy_error', 'Invalid policy field: ' + name)
    for name in ('validation_reserve_seconds', 'cleanup_reserve_seconds'):
        if type(value[name]) not in (int, float) or not math.isfinite(value[name]) or value[name] < 0:
            raise Failure('policy_error', 'Invalid policy field: ' + name)
    if type(value['skills_enabled']) is not bool:
        raise Failure('policy_error', 'skills_enabled must be boolean')


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
