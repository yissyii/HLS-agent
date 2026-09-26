"""Public isolation, bounded requests and enforced withholding for contract generation."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agent.selftest.bench4hls_contract_generation import generate_contract, generate_bindings
from agent.selftest.bench4hls_competition import run_task
from tools import test_bench4hls_selftest_competition as support


PROBLEM = ('An 8-bit register: each call captures d, or synchronously resets to zero '
           'when reset is high. q observes the updated state.\n'
           'void TopModule(ap_uint<8> d, bool reset, ap_uint<8> &q)')


def specification():
    return dict(schema_version=1, decision='ready', nodes=[],
        state=[dict(name='s0', width=8, signed=False, update='i0', enable='true',
                    reset=dict(condition='i1', value=0, priority='reset_first'), evidence=['P000'])],
        outputs=[dict(name='q', value='s0', observe='after', when='true', evidence=['P000'])],
        excluded=[], reasons=[])


class Responses:
    def __init__(self, values):
        self.values = list(values)
        self.prompts = []

    def generate(self, prompt, runtime, directory, deadline):
        self.prompts.append(prompt)
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        text = value if isinstance(value, str) else json.dumps(value)
        (Path(directory) / 'response.txt').write_text(text, encoding='utf-8')
        return dict(status='passed', requests=1, usage={'total_tokens': 7})


class ContractGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def generate(self, responses, *, problem=PROBLEM, generator=generate_contract):
        model = Responses(responses)
        result = generator(problem.encode(), 'TopModule', self.root / 'generation',
            model=model, runtime={}, deadline=time.monotonic()+30, max_calls=64, seed=12)
        return result, model

    def test_consistency_preserves_experimental_status_and_bound_artifacts(self):
        result, model = self.generate([specification(), specification()])
        self.assertEqual(result['status'], 'generated_unreviewed')
        self.assertEqual(result['model_requests'], 2)
        self.assertEqual(result['review']['status'], 'consistent_on_probes')
        self.assertFalse(result['repair_feedback_allowed'])
        self.assertFalse(result['review']['automatic_acceptance_allowed'])
        self.assertEqual(result['review']['source_sha256'], result['source_sha256'])
        self.assertTrue((self.root / 'generation/selftest.cpp').is_file())
        self.assertLessEqual(result['coverage']['calls'], 64)
        self.assertTrue(result['usage_complete'])
        self.assertEqual(result['total_tokens_reported'], 14)
        self.assertFalse(model.prompts[1].context['other_contract_visible'])

    def test_schema_retry_is_bounded_and_independent_payload_is_blind(self):
        invalid = specification()
        invalid['state'][0]['update'] = 'n0'
        fixed = specification()
        fixed.update(decision='partial', excluded=['PRIMARY_ONLY_MARKER'])
        result, model = self.generate([invalid, fixed, specification()])
        self.assertEqual(result['status'], 'generated_unreviewed')
        self.assertEqual(result['model_requests'], 3)
        self.assertEqual(result['attempts'][0]['status'], 'invalid_schema')
        self.assertIn('schema_feedback', json.loads(model.prompts[1].text))
        independent = json.loads(model.prompts[2].text)
        self.assertNotIn('schema_feedback', independent)
        self.assertNotIn('PRIMARY_ONLY_MARKER', model.prompts[2].text)
        self.assertNotIn('specification', independent)

    def test_bindings_preserve_raw_and_expanded_graph_and_blind_review(self):
        spec = dict(schema_version=1, decision='ready', components=[dict(id='c0', kind='register',
            data='i0', enable=None, reset=dict(input='i1', active=1, value=0, priority='reset_first'),
            mask=None, evidence=['P000'])], outputs=[dict(name='q', value='c0', observe='after',
            evidence=['P000'])], excluded=[], reasons=[])
        result, model = self.generate([spec, spec], generator=generate_bindings)
        self.assertEqual(result['status'], 'generated_unreviewed')
        self.assertEqual(result['generation_mode'], 'bindings')
        self.assertEqual(result['representation'], 'bindings')
        self.assertFalse(result['repair_feedback_allowed'])
        saved = json.loads((self.root / 'generation/specification.json').read_text())
        graph = json.loads((self.root / 'generation/expanded_graph.json').read_text())
        self.assertEqual(saved, spec)
        self.assertNotIn('components', graph)
        self.assertEqual(graph['state'][0]['reset']['condition'], 'i1')
        self.assertEqual(result['review']['primary_expanded_graph_sha256'], result['expanded_graph_sha256'])
        self.assertTrue((self.root / 'generation/independent_000/expanded_graph.json').is_file())
        self.assertEqual(result['model_requests'], 2)
        self.assertNotIn('schema_feedback', json.loads(model.prompts[1].text))
        self.assertIn('reverse_units', model.prompts[1].system)

    def test_two_schema_failures_do_not_keep_retrying(self):
        result, model = self.generate(['{}', '{}'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['category'], 'contract_schema_invalid')
        self.assertEqual(len(model.prompts), 2)
        self.assertFalse((self.root / 'generation/selftest.cpp').exists())

    def test_disputed_phase_has_counterexample_and_no_repair_permission(self):
        changed = specification()
        changed['outputs'][0]['observe'] = 'before'
        result, _ = self.generate([specification(), changed])
        self.assertEqual(result['review']['status'], 'conflict')
        self.assertEqual(result['review']['counterexample']['kind'], 'value_disagreement')
        self.assertFalse(result['repair_feedback_allowed'])

    def test_no_known_checks_cannot_emit_always_passing_cpp(self):
        raw = specification()
        raw['outputs'][0]['when'] = 'false'
        result, model = self.generate([raw])
        self.assertEqual(result['category'], 'no_known_output_checks')
        self.assertEqual(len(model.prompts), 1)
        self.assertFalse((self.root / 'generation/selftest.cpp').exists())

    def test_independent_failure_preserves_primary_and_does_not_promote(self):
        result, model = self.generate([specification(), OSError('synthetic transport failure')])
        self.assertEqual(result['status'], 'generated_unreviewed')
        self.assertEqual(result['review']['status'], 'failed')
        self.assertEqual(len(model.prompts), 2)
        self.assertFalse(result['usage_complete'])
        self.assertFalse(result['repair_feedback_allowed'])
        self.assertTrue((self.root / 'generation/generation_000/contract.json').is_file())

    def test_unsupported_interface_never_calls_model(self):
        result, model = self.generate([], problem='void TopModule(float a, float &q)')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(model.prompts, [])

    def test_competition_enforces_contract_withholding_even_if_receipt_claims_permission(self):
        self._check_withheld('contract')

    def test_competition_enforces_bindings_withholding_even_if_receipt_claims_permission(self):
        self._check_withheld('bindings')

    def _check_withheld(self, mode):
        fixture = support.CompetitionTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        model = Responses(['void TopModule(int a,int&z){z=a+1;}'])
        selftest = support.FakeSelftestValidator()
        official = support.FakeOfficialValidator()
        generation = dict(status='generated_unreviewed', model_requests=0,
                          repair_feedback_allowed=True)
        with patch('agent.selftest.bench4hls_competition.generate_testbench', return_value=generation):
            result = run_task(fixture.task, fixture.root / 'out', config=fixture.runtime,
                policy=fixture.policy, model=model, selftest_validator=selftest,
                official_validator=official, selftest_mode=mode)
        self.assertEqual(selftest.calls, [])
        self.assertFalse(result['selftest_feedback']['allowed'])
        self.assertEqual(result['development_scope'], 'generation_only_selftest_withheld')
        self.assertTrue(result['official_passed'])
        self.assertEqual(len(model.prompts), 1)


if __name__ == '__main__':
    unittest.main()
