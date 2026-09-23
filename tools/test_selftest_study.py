"""Study protocol tests; no external model or toolchain calls."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.prepare_selftest_study import prepare
from tools.run_selftest_study import freeze,generate,evaluate,summarize,check_protocol
from agent.selftest.spec import SelftestError


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='selftest-study-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.dataset=prepare(self.root/'data')
        self.protocol=freeze(self.dataset,ROOT/'agent/selftest/config/runtime.wsl.example.json',self.root/'freeze')
        self.protocol_path=self.root/'freeze/protocol.json'

    def test_partition_and_freeze(self):
        m=check_protocol(self.protocol,self.dataset)
        self.assertEqual(sum(t['split']=='development' for t in m['tasks']),5)
        self.assertEqual(sum(t['split']=='holdout' for t in m['tasks']),5)
        changed=copy.deepcopy(self.protocol);changed['max_cases']=1
        with self.assertRaises(SelftestError):check_protocol(changed,self.dataset)

    def test_code_change_rejects(self):
        with patch('tools.run_selftest_study.source_snapshot',return_value={}):
            with self.assertRaises(SelftestError):check_protocol(self.protocol,self.dataset)

    def test_bounded_policy_is_frozen_and_forwarded(self):
        protocol=freeze(self.dataset,ROOT/'agent/selftest/config/runtime.wsl.example.json',
                        self.root/'bounded',planning_policy='bounded-v2')
        self.assertEqual(protocol['planning_policy'],'bounded-v2')
        with patch('tools.run_selftest_study.generate_suite',return_value=dict(status='failed',elapsed_seconds=0,generation_requests=0)) as invoke:
            generate(self.dataset,self.root/'bounded/protocol.json',self.root/'bounded_generation','development')
        self.assertEqual(len(invoke.call_args_list),5)
        self.assertTrue(all(call.kwargs['planning_policy']=='bounded-v2' for call in invoke.call_args_list))

    def test_holdout_requires_matching_development_report(self):
        with self.assertRaises(SelftestError):
            generate(self.dataset,self.protocol_path,self.root/'blocked','holdout')
        bad=self.root/'bad.json'
        bad.write_text(json.dumps(dict(split='development',protocol_id='wrong',complete=True)))
        with self.assertRaises(SelftestError):
            generate(self.dataset,self.protocol_path,self.root/'blocked','holdout',bad)

    def test_all_generation_failures_preserved_without_private_reads(self):
        class InvalidModel:
            def generate(self,prompt,runtime,directory,deadline):
                (directory/'response.txt').write_text('{}')
                return dict(status='passed')
        original=Path.read_text
        def guarded(path,*args,**kwargs):
            if 'private' in path.parts or path.name=='fixtures.json':
                raise AssertionError('Generation touched private fixtures')
            return original(path,*args,**kwargs)
        with patch.object(Path,'read_text',guarded):
            result=generate(self.dataset,self.protocol_path,self.root/'generated','development',model=InvalidModel())
        self.assertEqual(len(result['tasks']),5)
        self.assertTrue(all(t['status']=='failed' for t in result['tasks']))
        report=evaluate(self.dataset,self.root/'generated',self.root/'eval')
        self.assertEqual(report['metrics']['generation_failures'],5)
        self.assertEqual(report['metrics']['controls_not_evaluated'],5)
        self.assertIsNone(report['metrics']['mutation_detection_rate'])

    def test_metric_denominators(self):
        def row(name,status,control=None,mutants=()):
            return dict(id=name,generation_status=status,control_status=control,generation_requests=1,
                        variants=[dict(role='mutant',status=s) for s in mutants])
        m=summarize([row('failed','failed'),row('false','generated_unreviewed','selftest_failed',('selftest_failed',)),
                     row('ok','generated_unreviewed','selftest_passed',('selftest_failed','inconclusive','selftest_passed'))])
        self.assertEqual(m['generation_success_rate'],2/3)
        self.assertEqual(m['false_positive_rate_completed_controls'],1/2)
        self.assertEqual(m['eligible_mutants'],3)
        self.assertEqual(m['functional_kills'],1)
        self.assertEqual(m['mutation_detection_rate'],1/3)
        self.assertEqual(m['mutation_inconclusive'],1)


if __name__=='__main__':unittest.main()
