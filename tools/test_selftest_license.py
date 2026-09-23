"""License amendment must preserve model results and frozen suite identities."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.prepare_selftest_study import prepare
from tools.prepare_selftest_examples import EXAMPLES,response_for
from tools.run_selftest_study import freeze,generate,read,check_protocol
from tools.amend_selftest_license import amend
from agent.selftest.generate import load_suite
from agent.selftest.spec import SelftestError


class LicenseAmendmentTest(unittest.TestCase):
    def test_only_validation_license_and_lineage_change(self):
        with tempfile.TemporaryDirectory(prefix='selftest-license-') as temp:
            root=Path(temp);dataset=prepare(root/'dataset')
            config=ROOT/'agent/selftest/config/runtime.wsl.example.json'
            original=freeze(dataset,config,root/'freeze',backend='vitis',evaluation_config=config)
            class FixtureModel:
                def generate(self,prompt,runtime,directory,deadline):
                    public=json.loads(prompt.text)
                    item=next(x for x in EXAMPLES if public['public_problem'].strip()==x['problem'])
                    bundle=response_for(item)
                    answer=bundle['test_plan' if 'contract' in public else 'contract']
                    (directory/'response.txt').write_text(json.dumps(answer))
                    return {'status':'passed'}
            before=generate(dataset,root/'freeze/protocol.json',root/'before','development',model=FixtureModel())
            new_id=amend(dataset,root/'before',root/'after','/approved/working.lic')
            after=read(root/'after/protocol.json')
            self.assertNotEqual(new_id,original['protocol_id'])
            self.assertEqual(after['amendment']['previous_protocol_id'],original['protocol_id'])
            self.assertEqual(after['runtime'],original['runtime'])
            self.assertEqual(after['source_files'],original['source_files'])
            self.assertEqual(after['amendment']['model_requests_added'],0)
            self.assertEqual(before['tasks'],read(root/'after/generation_summary.json')['tasks'])
            check_protocol(after,dataset)
            for task in before['tasks']:
                self.assertEqual(load_suite(root/'before/suites'/task['id'])[0]['suite_id'],
                                 load_suite(root/'after/suites'/task['id'])[0]['suite_id'])
            with self.assertRaises(SelftestError):
                amend(dataset,root/'after',root/'unchanged','/approved/working.lic')


if __name__=='__main__':unittest.main()
