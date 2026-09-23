"""Opt-in bounded planner. Preserve requests; never silently drop explicit cases."""
import copy
import itertools
import math
import random

from .oracle import expand
from .spec import integer, keys, require


POLICY = 'bounded-v2'


def plan_bounded(requested, contract, max_cases=4096):
    integer(max_cases, 1, 65536, 'max_cases')
    keys(requested, {'schema_version', 'oracle', 'explicit_cases', 'sampling'}, 'requested plan')
    require(type(requested['schema_version']) is int and requested['schema_version'] == 1,
            'Bounded planner expects a model schema-v1 plan')
    plan = copy.deepcopy(requested)
    sample = plan['sampling']
    keys(sample, {'mode', 'seed', 'random_cases'}, 'requested sampling')
    require(sample['mode'] in {'exhaustive', 'boundary_random'}, 'Unknown sampling mode')
    integer(sample['seed'], 0, 2**32-1, 'seed')
    integer(sample['random_cases'], 0, 65536, 'random_cases')
    require(sample['mode'] != 'exhaustive' or sample['random_cases'] == 0,
            'Exhaustive mode requires random_cases=0')
    cases = plan['explicit_cases']
    require(isinstance(cases, list) and len(cases) <= 65536, 'Invalid explicit_cases')
    normalized = []
    for index, case in enumerate(cases):
        keys(case, {'inputs', 'rule_ids'}, 'explicit case')
        values = case['inputs']
        require(isinstance(values, list) and len(values) == len(contract['inputs']), 'Test input arity mismatch')
        for position, param in enumerate(contract['inputs']):
            if param['type'] == 'bool' and type(values[position]) is bool:
                values[position] = int(values[position])
                normalized.append({'case': index, 'parameter': position})
    # Check ALL explicit cases, refs, types and oracle results before selecting inputs.
    probe = copy.deepcopy(plan)
    probe['schema_version'] = 2
    probe['sampling'] = dict(mode='explicit', seed=sample['seed'], random_cases=0)
    if not cases:
        probe['explicit_cases'] = [dict(inputs=[p['domain'][0] for p in contract['inputs']],
                                        rule_ids=plan['oracle']['rule_ids'])]
    checked, _ = expand(probe, contract, 65536)
    explicit = checked if cases else []
    require(len(explicit) <= max_cases, 'Explicit cases exceed budget; none may be dropped')
    selected = {tuple(v['inputs']): dict(inputs=v['inputs'], rule_ids=v['rule_ids']) for v in explicit}
    origins = {k: 'explicit' for k in selected}
    def add(values, origin):
        key = tuple(values)
        if key not in selected and len(selected) < max_cases:
            selected[key] = dict(inputs=list(values), rule_ids=list(plan['oracle']['rule_ids']))
            origins[key] = origin
    domains = [p['domain'] for p in contract['inputs']]
    size = math.prod(hi-lo+1 for lo, hi in domains)
    rng = random.Random(sample['seed'])
    boundary_total = boundary_selected = random_attempts = 0
    if size <= max_cases:
        mode = 'exhaustive'
        for values in itertools.product(*(range(lo, hi+1) for lo, hi in domains)):
            add(values, 'exhaustive')
    else:
        mode = 'boundary_random'
        axes = [sorted({v for v in (lo, lo+1, -1, 0, 1, hi-1, hi) if lo <= v <= hi}) for lo, hi in domains]
        boundaries = list(itertools.product(*axes))
        boundary_total = len(boundaries)
        missing = [v for v in boundaries if v not in selected]
        room = max_cases-len(selected)
        # When the full boundary product will not fit, reserve half for broad sampling.
        limit = len(missing) if len(missing) <= room else (room+1)//2
        chosen = missing if len(missing) <= limit else rng.sample(missing, limit)
        for values in chosen:
            add(values, 'boundary')
        boundary_selected = sum(v in selected for v in boundaries)
        room = max_cases-len(selected)
        target = room if sample['mode'] == 'exhaustive' else min(room, sample['random_cases'])
        before = len(selected)
        while len(selected)-before < target and random_attempts < max(32, 8*target):
            random_attempts += 1
            add([rng.randint(lo, hi) for lo, hi in domains], 'random')
    effective = dict(schema_version=2, oracle=plan['oracle'], explicit_cases=list(selected.values()),
                     sampling=dict(mode='explicit', seed=sample['seed'], random_cases=0))
    vectors, _ = expand(effective, contract, max_cases)
    adjustments = []
    if sample['mode'] != mode:
        adjustments.append('sampling_mode_selected_from_domain_and_budget')
    if normalized:
        adjustments.append('bool_inputs_normalized_only_for_bool_parameters')
    if boundary_selected < boundary_total:
        adjustments.append('boundary_product_subsampled_with_fixed_seed')
    if sample['mode'] == 'boundary_random' and sum(v == 'random' for v in origins.values()) < sample['random_cases']:
        adjustments.append('random_count_limited_by_budget_domain_or_duplicate_attempts')
    report = dict(policy=POLICY, max_cases=max_cases, domain_size=size, requested_sampling=requested['sampling'],
                  effective_mode=mode, case_count=len(vectors), normalized_bool_inputs=normalized,
                  explicit_unique_count=len(explicit), boundary_candidate_count=boundary_total,
                  boundary_selected_count=boundary_selected, random_attempts=random_attempts,
                  vector_origins=list(origins.values()), adjustments=adjustments,
                  semantic_correctness='unverified')
    return effective, report
