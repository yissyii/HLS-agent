"""Synthetic RAG release and repair contracts; no generation service or Vitis needed."""
import copy
import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.test_agent_contract import AgentContract, FakeModel, FakeValidator
from agent.context.builder import build
from agent.context.retrieval import Retrieval, make_query
from agent.core.contracts import Candidate, Diagnostic, TaskSpec
from agent.core.policy import RAG_DEFAULTS, validate_policy
from rag.common import canonical, file_sha256, record, sha256, write_corpus, write_json
from rag.release import load_release, verify_release, reference_records
from serve.inference import Failure, request_payload


class RAGContracts(unittest.TestCase):
    setUpClass = classmethod(AgentContract.setUpClass.__func__)
    tearDownClass = classmethod(AgentContract.tearDownClass.__func__)
    public_task = AgentContract.public_task
    solve = AgentContract.solve

    def setUp(self):
        AgentContract.setUp(self)
        name = 'test-' + uuid.uuid4().hex
        self.corpus = ROOT / 'rag/corpora' / name
        self.index = ROOT / 'rag/indexes' / name
        source = dict(document='fixture', version='2025.2', language='en-US', file='fixture.txt',
                      file_sha256='0'*64, page_start=1, page_end=1, section_path=['Recursion'],
                      parent_id='recursion', license='synthetic')
        self.record = record('fixture-recursion', 'Recursive Functions',
                             'Recursive functions cannot be synthesized. SYNTHETIC_REFERENCE', source,
                             kind='document_section', release={'status':'reference'})
        manifest = write_corpus(self.corpus, [self.record], {})
        self.index.mkdir(parents=True)
        # BM25 never loads vectors; this fixture still verifies all release checksums.
        (self.index/'vectors.npy').write_bytes(b'synthetic-bm25-only')
        im = dict(corpus_sha256=manifest['records_sha256'], fingerprint='fixture-index',
                  vectors_sha256=file_sha256(self.index/'vectors.npy'))
        write_json(self.index/'manifest.json', im)
        self.registry = self.directory/'release.json'
        self.release = dict(schema_version=1, release_id='synthetic-reference', status='reference',
                            corpus=str(self.corpus), index=str(self.index),
                            corpus_manifest_sha256=file_sha256(self.corpus/'manifest.json'),
                            records_sha256=manifest['records_sha256'],
                            index_manifest_sha256=file_sha256(self.index/'manifest.json'),
                            index_fingerprint=im['fingerprint'], vectors_sha256=im['vectors_sha256'])
        write_json(self.registry, self.release)
        self.rag_runtime = self.directory/'rag-runtime.json'
        write_json(self.rag_runtime, dict(registry=str(self.registry), python=sys.executable, model=None))

    def tearDown(self):
        for p, parent in ((self.corpus,ROOT/'rag/corpora'),(self.index,ROOT/'rag/indexes')):
            assert p.resolve().is_relative_to(parent.resolve())
            shutil.rmtree(p)
        AgentContract.tearDown(self)

    def enable(self):
        self.policy.update(rag_enabled=True, rag_mode='bm25')
        self.public_task(feedback='compiler_diagnostics')
        self.model = FakeModel(['int kernel(int a){return bad;}', 'int kernel(int a){return a+1;}'])
        self.validator = FakeValidator({(0,'csim'):'compile_error'})
        check = self.validator.check
        def with_diagnostic(*args):
            from dataclasses import replace
            result = check(*args)
            return replace(result, feedback_text='error: recursive functions cannot be synthesized')
        self.validator.check = with_diagnostic
        self.problem.write_text('Implement int kernel(int a), returning a + 1. Recursive functions are forbidden.')

    def test_disabled_has_identical_messages_and_no_runtime_read(self):
        old = {k:v for k,v in self.policy.items() if k not in RAG_DEFAULTS}
        validate_policy(old)
        task = TaskSpec(self.problem.read_bytes())
        before = build(task,self.config,old)
        after = build(task,self.config,self.policy)
        self.assertEqual(request_payload(before.text,self.config,system_prompt=before.system),
                         request_payload(after.text,self.config,system_prompt=after.system))
        with patch('agent.context.retrieval.load_runtime', side_effect=AssertionError('read while off')):
            self.assertFalse(Retrieval(self.policy,self.directory/'missing.json').enabled)
            code,_=self.solve(rag_runtime=self.directory/'missing.json')
            self.assertEqual(code,0)

    def test_repair_only_worker_and_injection_evidence(self):
        self.enable()
        code,result=self.solve(rag_runtime=self.rag_runtime)
        self.assertEqual((code,result['status']),(0,'passed'))
        self.assertNotIn('SYNTHETIC_REFERENCE',self.model.prompts[0].text)
        self.assertIn('SYNTHETIC_REFERENCE',self.model.prompts[1].text)
        self.assertEqual(self.model.prompts[0].system,self.model.prompts[1].system)
        evidence=json.loads((self.directory/'agent/candidates/001/retrieval.json').read_text())
        self.assertEqual(evidence['injected_ids'],['fixture-recursion'])
        self.assertEqual(result['rag_history'][0]['injected_ids'],evidence['injected_ids'])
        self.assertEqual(evidence['feedback_policy'],'compiler_diagnostics')
        self.assertFalse((self.directory/'agent/candidates/000/retrieval.json').exists())

    def test_query_uses_released_feedback_not_hidden_outcome(self):
        from agent.feedback.diagnostics import classify
        from evaluation.validator import HLSValidator
        from evaluation.task_io import load_task
        import time
        self.public_task()
        task=load_task(self.problem,self.manifest)
        candidate=Candidate(0,'int kernel(int a){return bad;}',self.directory/'c0/candidate.cpp',None)
        secret='HIDDEN_EXPECTED_1234'
        code_error='recursive functions cannot be synthesized'
        outcome=dict(status='failed',category='compile_error',diagnostic_tail=secret,compiler_text=code_error)
        validator=HLSValidator()
        with patch('evaluation.validator.validate_stage',return_value=outcome):
            for tier in ('category_only','compiler_diagnostics','public_diagnostics'):
                task.manifest['feedback_policy']=tier
                result=validator.check(candidate,task,self.config,'csim',time.monotonic()+10)
                diagnostic=classify(result)
                query,_=make_query(task.problem.decode(),diagnostic.feedback,1600)
                if tier!='public_diagnostics': self.assertNotIn(secret,query)
                if tier=='category_only': self.assertNotIn(code_error,query)
                if tier=='compiler_diagnostics': self.assertIn(code_error,query)

    def test_pending_and_staging_are_rejected(self):
        pending=copy.deepcopy(self.record); pending['release']['status']='pending'
        with self.assertRaisesRegex(ValueError,'pending'):
            reference_records([pending])
        release=copy.deepcopy(self.release); release['corpus']=str(ROOT/'rag/staging/hidden')
        write_json(self.registry,release)
        with self.assertRaisesRegex(ValueError,'staging'):
            load_release(self.registry)

    def test_unregistered_staging_cannot_appear_in_results(self):
        self.enable()
        stage=ROOT/'rag/staging'/('test-'+uuid.uuid4().hex)
        stage.mkdir(parents=True)
        try:
            (stage/'secret.txt').write_text('Recursive functions STAGING_ONLY_MARKER')
            _,result=self.solve(rag_runtime=self.rag_runtime)
            self.assertEqual(result['status'],'passed')
            self.assertNotIn('STAGING_ONLY_MARKER',self.model.prompts[1].text)
        finally:
            assert stage.resolve().is_relative_to((ROOT/'rag/staging').resolve())
            shutil.rmtree(stage)

    def test_release_tamper_stops_before_generation(self):
        self.enable()
        with (self.corpus/'records.jsonl').open('ab') as f: f.write(b' ')
        code,result=self.solve(rag_runtime=self.rag_runtime)
        self.assertEqual(code,1)
        self.assertEqual(result['category'],'rag_configuration_error')
        self.assertEqual(self.model.prompts,[])

    def test_budget_drops_rag_before_diagnostic(self):
        from agent.feedback.diagnostics import classify
        candidate=Candidate(0,'int kernel(int a){return a;}',self.directory/'c.cpp',None)
        diagnostic=Diagnostic('compile_error','csim','released feedback '*30,'fixture',True)
        task=TaskSpec(self.problem.read_bytes())
        base=build(task,self.config,self.policy,candidate,diagnostic)
        config=copy.deepcopy(self.config)
        config['model']['context_tokens']=config['model']['max_tokens']+self.policy['context_safety_tokens']+base.context['budgeted_input_bytes']+10
        evidence={'hits':[{'record':self.record,'context':'X'*4000}],'release_id':'fixture'}
        prompt=build(task,config,self.policy,candidate,diagnostic,retrieval=evidence)
        self.assertEqual(prompt.messages,base.messages)
        self.assertEqual(prompt.context['rag_candidate_ids'],[])
        self.assertIn(diagnostic.feedback,prompt.text)

    def test_timeout_is_recorded_and_no_repair_request_sent(self):
        self.enable()
        with patch('agent.context.retrieval.run_process',return_value={'timed_out':True,'exit_code':1}):
            code,result=self.solve(rag_runtime=self.rag_runtime)
        self.assertEqual(code,1)
        self.assertEqual(result['category'],'rag_timeout')
        self.assertEqual(len(self.model.prompts),1)
        self.assertEqual(result['rag_history'][0]['status'],'failed')

    def test_query_bound_retains_diagnostic_and_policy_validation(self):
        query,flags=make_query('a'*5000,'b'*4000,128)
        self.assertLessEqual(len(query),128)
        self.assertIn('bbbb',query)
        self.assertTrue(flags['problem_trimmed'])
        bad=copy.deepcopy(self.policy); bad['rag_recall_k']=1; bad['rag_top_k']=3
        with self.assertRaises(Failure): validate_policy(bad)

    def test_bm25_freezes_release_without_requiring_model(self):
        from agent.context.retrieval import input_artifacts
        self.enable()
        write_json(self.rag_runtime, dict(registry=str(self.registry), python=sys.executable,
                                         model=str(self.directory/'absent-model')))
        paths = input_artifacts(self.policy, self.rag_runtime)
        self.assertIn(self.corpus/'records.jsonl', paths)
        self.assertIn(self.index/'vectors.npy', paths)
        self.assertIn(self.registry, paths)
        self.assertFalse(any('absent-model' in str(p) for p in paths))

    def test_loopback_request_contains_only_repair_references(self):
        self.enable()
        type(self).response_mode='repair'
        code,result=self.solve(model=None,rag_runtime=self.rag_runtime)
        self.assertEqual((code,result['status']),(0,'passed'))
        self.assertEqual(len(self.requests),2)
        self.assertNotIn('SYNTHETIC_REFERENCE',self.requests[0]['messages'][1]['content'])
        self.assertIn('SYNTHETIC_REFERENCE',self.requests[1]['messages'][1]['content'])
        context=json.loads((self.directory/'agent/candidates/001/context.json').read_text())
        self.assertEqual(context['retrieval']['injected_ids'],['fixture-recursion'])
        self.assertLessEqual(context['rag_injected_bytes'],self.policy['rag_max_bytes'])

    def test_category_only_feedback_skips_worker_but_repairs(self):
        self.enable()
        self.validator = FakeValidator({(0,'csim'):'functional_or_runtime_error'})
        with patch('agent.context.retrieval.run_process', side_effect=AssertionError('worker must not run')):
            code, result = self.solve(rag_runtime=self.rag_runtime)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.model.prompts), 2)
        self.assertEqual(result['rag_history'][0]['status'], 'skipped')
        self.assertEqual(result['rag_history'][0]['skip_reason'], 'insufficient_diagnostic')
        self.assertEqual(self.model.prompts[1].context['retrieval']['status'], 'skipped')
        self.assertNotIn('<REFERENCE_MATERIAL>', self.model.prompts[1].text)

    def test_irrelevant_reference_is_rejected_and_repair_continues(self):
        self.enable()
        check = self.validator.check
        def unrelated(*args):
            from dataclasses import replace
            return replace(check(*args), feedback_text="error: no member 'parity' in 'ap_uint<100>'")
        self.validator.check = unrelated
        code, result = self.solve(rag_runtime=self.rag_runtime)
        self.assertEqual(code, 0)
        evidence = result['rag_history'][0]
        self.assertEqual(evidence['status'], 'no_reference_injected')
        self.assertEqual(evidence['injected_ids'], [])

    def test_legacy_policy_retains_old_query_and_no_skip(self):
        self.enable()
        self.policy['rag_strategy'] = 'legacy'
        self.validator = FakeValidator({(0,'csim'):'functional_or_runtime_error'})
        code, result = self.solve(rag_runtime=self.rag_runtime)
        self.assertEqual(code, 0)
        self.assertEqual(result['rag_history'][0]['status'], 'injected')
        self.assertTrue(result['rag_history'][0]['query'].startswith('Vitis HLS task:'))


if __name__=='__main__': unittest.main()
