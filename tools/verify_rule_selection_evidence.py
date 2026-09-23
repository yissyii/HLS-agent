"""Read-only request isolation/integrity check, writing only a new verification receipt."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.artifacts.writer import Artifacts
from agent.core.contracts import digest,json_digest
from agent.selftest.rules import RULES
from agent.selftest.spec import require
from tools.run_rule_selection_study import manifest,read,snapshot


def verify(dataset,generation,evaluation,output):
    dataset,generation,evaluation=map(lambda p:Path(p).resolve(),(dataset,generation,evaluation))
    data=manifest(dataset);protocol=read(generation/'protocol.json');summary=read(generation/'generation_summary.json');scored=read(evaluation/'evaluation.json')
    require(summary['complete'] and scored['complete'],'Runs incomplete')
    require(protocol['protocol_id']==json_digest({k:v for k,v in protocol.items() if k!='protocol_id'}),'Protocol fingerprint mismatch')
    require(scored['protocol_id']==summary['protocol_id']==protocol['protocol_id'],'Protocol mismatch')
    require(data['dataset_id']==protocol['dataset_id']==scored['dataset_id'],'Dataset mismatch')
    require(snapshot()==protocol['source_files'],'Working source changed')
    for name,hashed in protocol['source_files'].items():
        require(digest((generation/'source'/name).read_bytes())==hashed,'Frozen source copy changed')
    for name,hashed in data['private_files'].items():
        require(digest((dataset/'private'/name).read_bytes())==hashed,'Reference files changed')
    tasks={r['id']:r for r in data['tasks']}; counts={'selection':0,'contract':0,'plan':0}
    for request in generation.rglob('request.json'):
        relative=request.relative_to(generation); family,name,stage=relative.parts[:3]
        task=tasks[name]
        problem=(dataset/'public'/name/'problem.txt').read_text(encoding='utf-8')
        interface=(dataset/'public'/name/'interface.h').read_text(encoding='utf-8')
        require(digest(problem.encode())==task['problem_sha256'] and digest(interface.encode())==task['interface_sha256'],'Public task changed')
        messages=read(request)['messages'];require([m['role'] for m in messages]==['system','user'],'Unexpected model roles')
        payload=json.loads(messages[1]['content'])
        require(payload['public_problem']==problem and payload['public_interface']==interface,'Wrong public task in request')
        if family=='recommendations':
            require(set(payload)=={'public_problem','public_interface','registry','rule_version','applicability'},'Selection input leakage')
            require(payload['registry']==RULES,'Registry changed')
            counts['selection']+=1;prompt='recommend'
        else:
            require(family=='suites' and stage in {'generation_contract','generation_plan'},'Unexpected request origin')
            is_plan=stage=='generation_plan';prompt='plan' if is_plan else 'contract'
            require(set(payload)==({'public_problem','public_interface','max_cases','contract'} if is_plan else {'public_problem','public_interface','max_cases'}),'Generation input leakage')
            require(payload['max_cases']==protocol['max_cases'],'Generation budget changed')
            if is_plan:require(payload['contract']==read(generation/'suites'/name/'contract.json'),'Plan received something other than its own proposed contract')
            counts[prompt]+=1
        expected=protocol['source_files'][f'agent/selftest/prompts/{prompt}.md']
        require(digest(messages[0]['content'].encode())==expected,'Unfrozen system prompt')
        require('PRIVATE_REFERENCE_DO_NOT_SEND_v1' not in json.dumps(messages),'Private canary leaked')
    require(sum(counts.values())==scored['metrics']['transport_receipts'],'Missing request/receipt evidence')
    artifacts=Artifacts(output)
    result=dict(complete=True,protocol_id=protocol['protocol_id'],request_counts=counts,
        source_and_fixture_hashes_unchanged=True,public_payload_allowlist_passed=True,
        selector_result_not_forwarded_to_generator=True,private_reference_not_in_allowed_payload_fields=True,
        limitation='Checks artifact contents and payload construction; not an OS sandbox or external independent review.')
    artifacts.json('verification.json',result)
    print(json.dumps(result,indent=2));return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('dataset','generation','evaluation','output'):p.add_argument(name)
    a=p.parse_args();verify(a.dataset,a.generation,a.evaluation,a.output)
