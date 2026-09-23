"""Frozen internal real-model pilot. Generation does not load private reference labels."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.artifacts.writer import Artifacts
from agent.core.contracts import digest,json_digest
from agent.selftest.audit import audit_suite
from agent.selftest.generate import generate_suite,load_suite
from agent.selftest.oracle import Expression
from agent.selftest.recommend import recommend_rule
from agent.selftest.spec import PublicTask,identifier,require,type_info
from serve.agent_model import ModelClient
from serve.inference import load_config


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def snapshot():
    files=list((ROOT/'agent/selftest').glob('*.py'))+list((ROOT/'agent/selftest/prompts').glob('*.md'))
    files += [ROOT/p for p in ('serve/agent_model.py','serve/inference.py','evaluation/hls.py',
        'evaluation/lifecycle.py','agent/core/contracts.py','agent/artifacts/writer.py',
        'local_eval/guard.py','local_eval/retry.py','tools/prepare_rule_selection_study.py','tools/run_rule_selection_study.py')]
    return {p.relative_to(ROOT).as_posix():digest(p.read_bytes()) for p in sorted(files) if p.is_file()}


def manifest(dataset):
    data=read(dataset/'public_manifest.json')
    require(data['dataset_id']==json_digest({k:v for k,v in data.items() if k!='dataset_id'}),'Dataset manifest changed')
    ids=[identifier(t['id']) for t in data['tasks']]
    require(len(ids)==20 and len(set(ids))==20,'Require frozen 20-task pilot')
    return data


class PublicOnlyModel:
    """Payload allowlist: no fixture, candidate, selector result or audit-feedback field."""
    def __init__(self,task,client=None):self.task,self.client=task,client or ModelClient()
    def generate(self,prompt,runtime,directory,deadline):
        payload=json.loads(prompt.text)
        require(payload['public_problem']==self.task.problem and payload['public_interface']==self.task.interface,'Public input substitution')
        allowed={'public_problem','public_interface','max_cases','contract'} if 'registry' not in payload else {'public_problem','public_interface','registry','rule_version','applicability'}
        require(set(payload)<=allowed,'Unexpected model-visible field')
        require('PRIVATE_REFERENCE_DO_NOT_SEND_v1' not in prompt.text+prompt.system,'Private label leakage')
        return self.client.generate(prompt,runtime,directory,deadline)


def generate(dataset,config,output,workers=2):
    from evaluation.lifecycle import request_retry_settings
    require(request_retry_settings() is None,'Experiment requires no inherited transport retry policy')
    require(workers in (1,2),'Use at most two independent task workers')
    dataset=Path(dataset).resolve(); data=manifest(dataset)
    artifacts=Artifacts(output); frozen=snapshot(); started=time.monotonic()
    protocol=dict(schema_version=1,dataset_id=data['dataset_id'],source_files=frozen,
        runtime=load_config(config,allow_external=True),workers=workers,repeats=1,retries=0,max_requests=60,
        selector_timeout=180,generation_timeout=360,max_cases=4096,planning_policy='bounded-v2',
        input_policy='Selection sees public task+registry; generation sees public task and its own proposed contract, never selection or gold.',
        limitation='New author-created wording/widths; known task families. Not independent third-party, training-excluded, or formal correctness evaluation.')
    protocol['protocol_id']=json_digest(protocol)
    artifacts.json('protocol.json',protocol)
    for name in frozen:artifacts.bytes('source/'+name,(ROOT/name).read_bytes())
    results={}
    def process(item):
        require(snapshot()==frozen,'Source changed after freeze')
        task=PublicTask.load(dataset/'public'/item['id']/'problem.txt',dataset/'public'/item['id']/'interface.h')
        require(all(task.snapshot()[k]==item[k] for k in task.snapshot()),'Public task changed')
        client=PublicOnlyModel(task)
        selection=recommend_rule(task,artifacts.root/'recommendations'/item['id'],model=client,
                                  runtime=protocol['runtime'],timeout=protocol['selector_timeout'])
        generation=generate_suite(task,artifacts.root/'suites'/item['id'],model=client,runtime=protocol['runtime'],
            max_cases=protocol['max_cases'],timeout=protocol['generation_timeout'],planning_policy=protocol['planning_policy'])
        return dict(id=item['id'],selection=selection,generation=generation)
    def persist(complete=False):
        value=dict(protocol_id=protocol['protocol_id'],dataset_id=data['dataset_id'],complete=complete,
                   elapsed_seconds=round(time.monotonic()-started,3),tasks=[results[t['id']] for t in data['tasks'] if t['id'] in results])
        artifacts.json('generation_summary.json',value)
        return value
    persist()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(process,item):item['id'] for item in data['tasks']}
        for future in as_completed(futures):
            name=futures[future]
            try:results[name]=future.result()
            except Exception as error:results[name]=dict(id=name,worker_error=dict(type=type(error).__name__,message=str(error)))
            persist()
            row=results[name]
            print(f'{len(results)}/20 {name}: selection={row.get("selection",{}).get("status","worker_error")}, generation={row.get("generation",{}).get("status","worker_error")}',flush=True)
    require(snapshot()==frozen,'Source changed during generation')
    return persist(True)


def score_selection(proposal,reference):
    if proposal is None:return 'failed'
    decision=proposal['decision']
    if reference['expected_decision']=='propose':
        if decision!='propose':return 'false_abstention'
        if proposal['rule_id']==reference['rule_id'] and proposal['width']==reference['width'] and proposal['input_domain']==reference['domains'][0]:return 'correct_proposal'
        return 'wrong_proposal'
    if decision=='propose':return 'unsafe_proposal'
    if decision==reference['expected_decision']:return 'correct_abstention'
    return 'safe_but_wrong_abstention_reason'


def score_oracle(contract,plan,reference,points):
    if not reference['oracle_evaluable']:
        return dict(status='not_evaluable',reason=reference['reason'],unsafe_spec_assumption=True)
    domains=[p['domain'] for p in contract['inputs']]
    if domains!=reference['domains'] or [p['name'] for p in contract['inputs']]!=reference['names']:
        return dict(status='contract_mismatch',actual_domains=domains,expected_domains=reference['domains'])
    expr=Expression(plan['oracle']['expression'],reference['names']); low,high=type_info(contract['output_type'])
    mismatches=errors=0; first=None
    for values,expected in points:
        try:
            actual=expr(dict(zip(reference['names'],values)))
            require(low<=actual<=high,'Oracle output out of type range')
            if actual!=expected:
                mismatches+=1
                if first is None:first=dict(inputs=values,expected=expected,actual=actual)
        except (ValueError,TypeError,KeyError) as error:
            errors+=1
            if first is None:first=dict(inputs=values,error=str(error))
    return dict(status='correct' if mismatches==errors==0 else 'incorrect',checked=len(points),exhaustive=True,
                mismatches=mismatches,errors=errors,first_failure=first)


def evaluate(dataset,generation,output):
    dataset=Path(dataset).resolve(); generation=Path(generation).resolve()
    data=manifest(dataset); protocol=read(generation/'protocol.json'); summary=read(generation/'generation_summary.json')
    require(protocol['protocol_id']==json_digest({k:v for k,v in protocol.items() if k!='protocol_id'}),'Protocol changed')
    require(snapshot()==protocol['source_files'],'Source differs from frozen generation')
    require(summary['complete'] and summary['protocol_id']==protocol['protocol_id'] and data['dataset_id']==protocol['dataset_id'],'Incomplete or wrong generation')
    require([r['id'] for r in summary['tasks']]==[t['id'] for t in data['tasks']],'Missing or reordered tasks')
    # Successful suites and recommendations are pinned BEFORE loading references.
    generated={r['id']:load_suite(generation/'suites'/r['id']) for r in summary['tasks'] if r.get('generation',{}).get('status')=='generated_unreviewed'}
    for row in summary['tasks']:
        if 'selection' in row:
            selected=read(generation/'recommendations'/row['id']/'recommendation.json')
            require(selected==row['selection'],'Recommendation changed')
            require(selected['recommendation_id']==json_digest({k:v for k,v in selected.items() if k!='recommendation_id'}),'Recommendation receipt changed')
        if row['id'] in generated:require(generated[row['id']][0]['suite_id']==row['generation']['suite_id'],'Suite changed')
    for name,hashed in data['private_files'].items():require(digest((dataset/'private'/name).read_bytes())==hashed,'Private fixture changed')
    fixtures=read(dataset/'private/fixtures.json'); refs={r['id']:r for r in fixtures['tasks']}
    points={name:[] for name in refs}
    for line in (dataset/'private/truth.tsv').read_text().splitlines():
        name,x,y,expected=line.split('\t'); values=[int(x),int(y)][:len(refs[name]['names'])]
        points[name].append((values,int(expected)))
    for name,ref in refs.items():
        count=1
        for lo,hi in ref['domains']:count*=hi-lo+1
        require(len(points[name])==(count if ref['oracle_evaluable'] else 0),'Incomplete reference domain')
        require(len({tuple(v) for v,_ in points[name]})==len(points[name]),'Duplicate reference inputs')
    artifacts=Artifacts(output); rows=[]
    report=dict(schema_version=1,complete=False,protocol_id=protocol['protocol_id'],dataset_id=data['dataset_id'],
                evaluation_kind='internal_new_task_pilot',tasks=rows,limitation=protocol['limitation'])
    artifacts.json('frozen_suites.json',{name:v[0]['suite_id'] for name,v in generated.items()})
    for row in summary['tasks']:
        name=row['id']; ref=refs[name]; selected=row.get('selection',{}); proposal=selected.get('proposal')
        selection_score=score_selection(proposal,ref)
        raw_file=generation/'recommendations'/name/'proposal.json'
        raw_proposal=read(raw_file) if raw_file.is_file() else None
        raw_decision=raw_proposal.get('decision') if isinstance(raw_proposal,dict) else None
        result=dict(id=name,group=ref['group'],expected_decision=ref['expected_decision'],selection_score=selection_score,
                    raw_decision=raw_decision,
                    expected_rule=ref['rule_id'],expected_width=ref['width'],selection_status=selected.get('status','worker_error'),
                    generation_status=row.get('generation',{}).get('status','worker_error'),
                    audit_status='not_run',oracle=dict(status='not_generated'))
        if name in generated:
            manifest_value,contents,contract,vectors=generated[name]
            plan=json.loads(contents['test_plan.json'])
            result['oracle']=score_oracle(contract,plan,ref,points[name])
            if proposal and proposal['decision']=='propose':
                if len(contract['inputs'])!=1 or contract['inputs'][0]['domain']!=proposal['input_domain']:
                    result['audit_skip_reason']='Recommendation and generated contract domains disagree'
                else:
                    binding=dict(schema_version=1,suite_id=manifest_value['suite_id'],rule_id=proposal['rule_id'],width=proposal['width'],evidence=proposal['evidence'])
                    artifacts.json(f'bindings/{name}.json',binding)
                    audit=audit_suite(generation/'suites'/name,artifacts.root/'audits'/name,binding=artifacts.root/'bindings'/f'{name}.json')
                    result.update(audit_status=audit['status'],audit_evidence_scope=audit['evidence_scope'],
                        audit_purpose='Experimental conditional comparison only; not an approved production binding',
                        automatic_repair_allowed=audit['automatic_repair_allowed'])
        rows.append(result)
        artifacts.json('evaluation.json',report)
        print(f'{name}: selection={selection_score}, oracle={result["oracle"]["status"]}, audit={result["audit_status"]}',flush=True)
    def count(predicate):return sum(predicate(r) for r in rows)
    receipts=[read(p) for p in generation.rglob('response.txt.meta.json')]
    report['metrics']=dict(tasks=len(rows),applicable_tasks=count(lambda r:r['expected_decision']=='propose'),
        correct_proposals=count(lambda r:r['selection_score']=='correct_proposal'),
        false_abstentions=count(lambda r:r['selection_score']=='false_abstention'),
        wrong_proposals=count(lambda r:r['selection_score']=='wrong_proposal'),
        nonapplicable_tasks=count(lambda r:r['expected_decision']!='propose'),
        correct_abstentions=count(lambda r:r['selection_score']=='correct_abstention'),
        safe_wrong_reason=count(lambda r:r['selection_score']=='safe_but_wrong_abstention_reason'),
        unsafe_proposals=count(lambda r:r['selection_score']=='unsafe_proposal'),
        raw_proposals_on_nonapplicable=count(lambda r:r['expected_decision']!='propose' and r['raw_decision']=='propose'),
        selection_failures=count(lambda r:r['selection_score']=='failed'),
        generated=count(lambda r:r['generation_status']=='generated_unreviewed'),
        blocked_specification=count(lambda r:r['generation_status']=='blocked_specification'),
        generation_other_failures=count(lambda r:r['generation_status'] not in {'generated_unreviewed','blocked_specification'}),
        numeric_reference_tasks=sum(r['oracle_evaluable'] for r in refs.values()),
        oracle_correct=count(lambda r:r['oracle']['status']=='correct'),oracle_incorrect=count(lambda r:r['oracle']['status']=='incorrect'),
        contract_mismatch=count(lambda r:r['oracle']['status']=='contract_mismatch'),
        unsafe_spec_assumptions=count(lambda r:r['oracle'].get('unsafe_spec_assumption',False)),
        audited=count(lambda r:r['audit_status']!='not_run'),
        unsafe_support=count(lambda r:r['audit_status']=='supported' and r['oracle']['status']!='correct'),
        incorrect_oracles_audited_correct_binding=count(lambda r:r['selection_score']=='correct_proposal' and r['oracle']['status']=='incorrect' and r['audit_status']!='not_run'),
        incorrect_oracles_caught_correct_binding=count(lambda r:r['selection_score']=='correct_proposal' and r['oracle']['status']=='incorrect' and r['audit_status']=='conflict'),
        correct_oracles_rejected_correct_binding=count(lambda r:r['selection_score']=='correct_proposal' and r['oracle']['status']=='correct' and r['audit_status']=='conflict'),
        wire_requests=sum(r.get('requests',0) for r in receipts),
        transport_receipts=len(receipts),transport_retries=sum(r.get('transport_retries',0) for r in receipts),
        transport_unknown=sum(r.get('request_outcome_unknown',False) or r.get('status')=='sending'
                              or r.get('category')=='api_network_or_timeout' and bool(r.get('requests')) for r in receipts))
    require(report['metrics']['wire_requests']<=60 and report['metrics']['transport_retries']==0,'Request protocol violation')
    require(snapshot()==protocol['source_files'],'Source changed during evaluation')
    report['complete']=True;artifacts.json('evaluation.json',report)
    print(json.dumps(report['metrics'],indent=2));return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    g=sub.add_parser('generate');g.add_argument('dataset');g.add_argument('config');g.add_argument('output');g.add_argument('--workers',type=int,default=2);g.add_argument('--allow-model-calls',action='store_true')
    e=sub.add_parser('evaluate');e.add_argument('dataset');e.add_argument('generation');e.add_argument('output')
    a=p.parse_args()
    if a.command=='generate':
        if not a.allow_model_calls:p.error('Explicit --allow-model-calls required (at most 60 requests)')
        generate(a.dataset,a.config,a.output,a.workers)
    else:evaluate(a.dataset,a.generation,a.output)
