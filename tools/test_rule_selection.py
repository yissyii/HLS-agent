"""Offline selector/experiment contracts. Fake transport tests are not model scores."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from agent.core.contracts import PromptBundle
from agent.selftest.recommend import make_prompt,recommend_rule,validate_proposal
from agent.selftest.spec import PublicTask,SelftestError
from tools.prepare_rule_selection_study import cases,prepare
from tools.run_rule_selection_study import PublicOnlyModel,score_selection,score_oracle


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='rule-selection-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.task=PublicTask('Stateless function f reverses all five bits of x. Legal x is 0 through 31 inclusive.','uint16_t f(uint16_t x);')
        self.proposal=dict(schema_version=1,decision='propose',rule_id='reverse_bits',width=5,input_domain=[0,31],
            evidence=[dict(source='problem',quote=self.task.problem)],rationale='The full five-bit word is reversed.',uncertainties=[])

    def test_valid_proposal_is_not_approval(self):
        result=recommend_rule(self.task,self.root/'result',response=self.proposal)
        self.assertEqual(result['status'],'recommendation_unreviewed')
        self.assertEqual(result['semantic_match'],'unverified')
        self.assertFalse(result['automatic_binding_allowed'])
        self.assertEqual(result['model_requests'],0)

    def test_abstentions(self):
        for decision in ('unsupported','uncertain'):
            value=dict(self.proposal,decision=decision,rule_id=None,width=None,input_domain=None,
                       uncertainties=['Missing width definition'] if decision=='uncertain' else [])
            self.assertEqual(validate_proposal(value,self.task)['decision'],decision)

    def test_invalid_shapes_and_claims(self):
        samples=[None,[],True,dict(self.proposal,rule_id='new_rule'),dict(self.proposal,width=True),
            dict(self.proposal,input_domain=[1,31]),dict(self.proposal,decision='approve'),
            dict(self.proposal,uncertainties=['I do not know']),dict(self.proposal,decision='unsupported'),
            dict(self.proposal,evidence=[dict(source='problem',quote='not in task')]),
            dict(self.proposal,evidence=[dict(source='interface',quote=self.task.interface)])]
        for i,sample in enumerate(samples):
            with self.subTest(sample=sample):
                # recommend_rule disambiguates recorded None as no response, still a safe failure.
                result=recommend_rule(self.task,self.root/f'bad{i}',response=sample)
                self.assertEqual(result['status'],'failed')
                self.assertFalse(result['automatic_binding_allowed'])

    def test_signed_input_rejects_proposal(self):
        task=PublicTask(self.task.problem,'uint16_t f(int16_t x);')
        with self.assertRaises(SelftestError):validate_proposal(self.proposal,task)

    def test_two_inputs_reject_proposal(self):
        task=PublicTask(self.task.problem,'uint16_t f(uint16_t x, uint16_t y);')
        with self.assertRaises(SelftestError):validate_proposal(self.proposal,task)

    def test_prompt_has_only_public_data_and_registry(self):
        payload=json.loads(make_prompt(self.task).text)
        self.assertEqual(set(payload),{'public_problem','public_interface','registry','rule_version','applicability'})
        self.assertNotIn('reference',payload)
        self.assertNotIn('oracle',payload)

    def test_transport_allowlist_rejects_private_fields(self):
        class Client:
            def generate(self,*args):return {'status':'passed'}
        wrapped=PublicOnlyModel(self.task,Client())
        prompt=make_prompt(self.task)
        self.assertEqual(wrapped.generate(prompt,{},self.root,1)['status'],'passed')
        payload=json.loads(prompt.text);payload['reference']='secret'
        bad=PromptBundle(json.dumps(payload),[],{},system=prompt.system)
        with self.assertRaises(SelftestError):wrapped.generate(bad,{},self.root,1)

    def test_live_transport_contract_without_network(self):
        proposal=copy.deepcopy(self.proposal)
        class Client:
            def generate(self,prompt,runtime,directory,deadline):
                (directory/'response.txt').write_text(json.dumps(proposal))
                return dict(status='passed',requests=1)
        runtime={'model':{'context_tokens':16384,'max_tokens':4096}}
        result=recommend_rule(self.task,self.root/'fake',model=Client(),runtime=runtime)
        self.assertEqual(result['model_requests'],1)
        self.assertEqual(result['status'],'recommendation_unreviewed')

    def test_corpus_counts_and_no_duplicate_public_tasks(self):
        rows=cases()
        self.assertEqual(len(rows),20)
        self.assertEqual(len({r['problem'] for r in rows}),20)
        self.assertEqual(sum(r['group']=='applicable' for r in rows),12)
        self.assertEqual(sum(r['group']=='confusable' for r in rows),4)
        self.assertEqual(sum(r['group']=='unsupported' for r in rows),4)
        self.assertEqual(sum(r['oracle_evaluable'] for r in rows),18)
        self.assertEqual(sum(r['expected_decision']=='uncertain' for r in rows),1)

    def test_selection_scoring_does_not_drop_failures(self):
        positive=dict(expected_decision='propose',rule_id='reverse_bits',width=5,domains=[[0,31]])
        negative=dict(expected_decision='unsupported')
        self.assertEqual(score_selection(self.proposal,positive),'correct_proposal')
        self.assertEqual(score_selection(dict(self.proposal,width=8),positive),'wrong_proposal')
        self.assertEqual(score_selection(dict(self.proposal,decision='unsupported'),positive),'false_abstention')
        self.assertEqual(score_selection(self.proposal,negative),'unsafe_proposal')
        self.assertEqual(score_selection(None,positive),'failed')

    def test_oracle_scoring_is_independent_of_audit_status(self):
        contract=dict(inputs=[dict(name='x',domain=[0,1])],output_type='uint16_t')
        ref=dict(oracle_evaluable=True,domains=[[0,1]],names=['x'])
        points=[([0],0),([1],1)]
        self.assertEqual(score_oracle(contract,{'oracle':{'expression':'x'}},ref,points)['status'],'correct')
        self.assertEqual(score_oracle(contract,{'oracle':{'expression':'0'}},ref,points)['status'],'incorrect')
        contract['inputs'][0]['domain']=[0,0]
        self.assertEqual(score_oracle(contract,{'oracle':{'expression':'0'}},ref,points)['status'],'contract_mismatch')

    @unittest.skipUnless(shutil.which('g++'),'Independent reference preparation needs g++')
    def test_native_reference_spots_and_domains(self):
        dataset=prepare(self.root/'dataset')
        fixtures=json.loads((dataset/'private/fixtures.json').read_text())
        tables={r['id']:{} for r in fixtures['tasks']}
        for line in (dataset/'private/truth.tsv').read_text().splitlines():
            name,x,y,value=line.split('\t');tables[name][int(x),int(y)]=int(value)
        for row in fixtures['tasks']:
            with self.subTest(name=row['id']):
                size=1
                for lo,hi in row['domains']:size*=hi-lo+1
                table=tables[row['id']]
                self.assertEqual(len(table),size if row['oracle_evaluable'] else 0)
                kind=row['kind']
                if kind==0:self.assertEqual(table[4,0],7)
                if kind==1:self.assertEqual(table[1,0],1<<(row['width']-1))
                if kind==2:self.assertEqual(table[(1<<row['width'])-1,0],row['width'])
                if kind==3:self.assertEqual(table[4,0],6)
                if kind==4:self.assertEqual(table[0x1234,0],0x3412)
                if kind==5:self.assertEqual(table[0xab12,0],0xab48)
                if kind==6:self.assertEqual(table[3,0],0)
                if kind==7:self.assertEqual(table[31,31],31)
                if kind==8:self.assertEqual(table[-1,0],8)


if __name__=='__main__':unittest.main(verbosity=2)
