"""Sidecar audits of frozen oracles. No candidate, model call, repair or truth vote."""
import random
from pathlib import Path

from agent.artifacts.writer import Artifacts
from agent.core.contracts import digest, json_digest
from .generate import load_suite
from .oracle import Expression
from .rules import RULES, RULE_VERSION, anchors, check_applicability, property_check, reference
from .spec import PublicTask, SelftestError, evidence, integer, keys, parse_json, require, type_info


DEFAULT_AUDIT_MAX_CASES = 65536
AUDITOR_VERSION = '0.2.1'


def audit_suite(suite, output, *, binding=None, anchor_file=None, max_cases=DEFAULT_AUDIT_MAX_CASES, seed=20260923):
    artifacts = Artifacts(output)
    report = dict(schema_version=2, auditor_version=AUDITOR_VERSION,
                  status='inconclusive', candidate_visible=False, model_requests=0,
                  evidence_scope='none', audit_complete=False,
                  manual_review_required=True, automatic_repair_allowed=False,
                  binding_semantics='not_independently_verified', official_correctness='not_evaluated',
                  checks=[], conflicts=[], errors=[], conflicts_truncated=False, oracle_evaluations=0)
    def conflict(item):
        if len(report['conflicts']) < 20:
            report['conflicts'].append(item)
        else:
            report['conflicts_truncated'] = True
    try:
        integer(max_cases, 1, 65536, 'audit max_cases')
        integer(seed, 0, 2**32-1, 'audit seed')
        require(binding is None or anchor_file is None, 'Choose rule binding OR supplied anchors per audit')
        manifest, data, contract, _ = load_suite(suite)
        report.update(suite_id=manifest['suite_id'], max_cases=max_cases, seed=seed,
                      rule_version=RULE_VERSION,
                      checker_sha256={p: digest(Path(__file__).with_name(p).read_bytes())
                                      for p in ('audit.py', 'rules.py', 'oracle.py', 'spec.py')})
        task = PublicTask(data['problem.txt'].decode(), data['interface.h'].decode())
        plan = parse_json(data['test_plan.json'].decode(), max_bytes=32_000_000)
        params = contract['inputs']
        expr = Expression(plan['oracle']['expression'], [p['name'] for p in params])
        def evaluate(values):
            report['oracle_evaluations'] += 1
            require(len(values) == len(params), 'Audit input arity mismatch')
            for value, param in zip(values, params):
                integer(value, *param['domain'], 'audit input')
            actual = expr(dict(zip((p['name'] for p in params), values)))
            integer(actual, *type_info(contract['output_type']), 'oracle output during audit')
            return actual
        def attachment(path, name):
            raw = Path(path).read_bytes()
            # Preserve even rejected evidence, not only successfully parsed objects.
            artifacts.bytes(name, raw)
            report[name + '_sha256'] = digest(raw)
            value = parse_json(raw.decode('utf-8'))
            require(isinstance(value, dict), 'Audit attachment must be a JSON object')
            require(value.get('suite_id') == manifest['suite_id'], 'Audit evidence belongs to a different suite')
            require(type(value.get('schema_version')) is int and value['schema_version'] == 1, 'Unsupported audit input')
            return value
        if anchor_file is not None:
            supplied = attachment(anchor_file, 'supplied_anchors.json')
            keys(supplied, {'schema_version', 'suite_id', 'anchors'}, 'anchor file')
            require(isinstance(supplied['anchors'], list) and 0 < len(supplied['anchors']) <= max_cases,
                    'Require nonempty anchors within audit budget')
            checked = 0
            for row in supplied['anchors']:
                keys(row, {'inputs', 'expected', 'evidence'}, 'anchor')
                evidence(row['evidence'], task)
                require(isinstance(row['inputs'], list), 'Anchor inputs must be list')
                integer(row['expected'], *type_info(contract['output_type']), 'anchor expected')
                actual = evaluate(row['inputs'])
                checked += 1
                if actual != row['expected']:
                    conflict(dict(kind='supplied_anchor', inputs=row['inputs'], expected=row['expected'], actual=actual))
            report['checks'].append(dict(kind='supplied_anchors', checked=checked,
                limitation='Quote presence does not prove supplied expected values; conditional consistency only.'))
            report.update(evidence_scope='supplied_anchors', audit_complete=True)
        if binding is not None:
            selected = attachment(binding, 'rule_binding.json')
            keys(selected, {'schema_version', 'suite_id', 'rule_id', 'width', 'evidence'}, 'rule binding')
            evidence(selected['evidence'], task)
            rule_id, width = selected['rule_id'], selected['width']
            check_applicability(rule_id, width, contract)
            report.update(rule_id=rule_id, rule_definition=RULES[rule_id],
                          rule_provenance='agent_authored_registry; external_human_review_pending')
            bound = 1 << width
            points = list(dict.fromkeys(x for x, _ in anchors(rule_id, width)))
            require(len(points) <= max_cases, 'Audit budget cannot fit mandatory rule anchors')
            if bound <= max_cases:
                points += [x for x in range(bound) if x not in points]
            else:
                rng = random.Random(seed)
                existing = set(points)
                # Width <=16 bounds this list and avoids unbounded rejection sampling.
                points += rng.sample([x for x in range(bound) if x not in existing], max_cases-len(points))
            exact_failures = property_failures = 0
            for x in points:
                actual = evaluate([x])
                expected = reference(rule_id, width, x)
                if actual != expected:
                    exact_failures += 1
                    conflict(dict(kind='bound_rule', inputs=[x], expected=expected, actual=actual))
                # An invalid intermediate cannot become a follow-up test outside the input domain.
                if not 0 <= actual < bound:
                    property_failures += 1
                    conflict(dict(kind='property_output_domain', inputs=[x], actual=actual))
                    continue
                witness = property_check(rule_id, width, x, actual, lambda v: evaluate([v]))
                if witness:
                    property_failures += 1
                    conflict(dict(kind='bound_property', **witness))
            report['checks'].append(dict(kind='bound_rule_and_properties', checked=len(points), domain_size=bound,
                exhaustive=len(points) == bound, exact_failures=exact_failures, property_failures=property_failures))
            report.update(evidence_scope='full_domain_rule' if len(points) == bound else 'sampled_rule',
                          audit_complete=True)
        if report['conflicts']:
            report['status'] = 'conflict'
        elif report['checks']:
            report['status'] = 'supported'
        else:
            report['errors'].append('No independent anchors or explicit rule binding supplied')
    except (SelftestError, OSError, ValueError, TypeError, KeyError) as error:
        report['errors'].append(str(error))
        report['status'] = 'conflict' if report['conflicts'] else 'inconclusive'
    if report['status'] == 'supported':
        report['conclusion'] = {
            'full_domain_rule': 'Consistent with the selected rule over its full declared domain; task-rule semantics remain unverified.',
            'sampled_rule': 'No conflict found at sampled rule inputs; unchecked inputs may still fail and task-rule semantics remain unverified.',
            'supplied_anchors': 'Consistent only with supplied anchors; their expected values and task semantics remain unverified.',
        }[report['evidence_scope']]
    elif report['status'] == 'conflict':
        report['conclusion'] = 'Conflict with supplied evidence; this does not establish whether the oracle, rule binding, or evidence is wrong.'
    else:
        report['conclusion'] = 'Insufficient evidence or incomplete/invalid audit; no correctness conclusion.'
    report['limitation'] = ('Checks are conditional on supplied evidence and rule applicability, not proof of natural-language intent. '
                            'No audit status authorizes candidate repair. Original suite is never changed.')
    report['audit_id'] = json_digest(report)
    artifacts.json('audit_result.json', report)
    return report
