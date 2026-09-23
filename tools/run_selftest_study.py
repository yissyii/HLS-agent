"""Frozen real-model pilot. Generation never reads fixtures.json or private source files."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.artifacts.writer import Artifacts
from agent.core.contracts import digest, json_digest
from agent.selftest.generate import generate_suite, load_suite
from agent.selftest.runner import run_suite
from agent.selftest.spec import PublicTask, identifier, require
from serve.agent_model import ModelClient
from serve.inference import load_config


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def source_snapshot():
    paths = list((ROOT/'agent/selftest').glob('*.py')) + list((ROOT/'agent/selftest/prompts').glob('*.md'))
    paths += [ROOT/'serve/agent_model.py', ROOT/'serve/inference.py', ROOT/'evaluation/hls.py',
              ROOT/'evaluation/validator.py', Path(__file__).resolve()]
    return {p.relative_to(ROOT).as_posix():digest(p.read_bytes()) for p in sorted(paths)}


def public_manifest(dataset):
    value = read(Path(dataset)/'public_manifest.json')
    require(value['dataset_id'] == json_digest({k:v for k,v in value.items() if k!='dataset_id'}), 'Dataset manifest changed')
    tasks = value['tasks']
    require(len({identifier(t['id']) for t in tasks}) == len(tasks), 'Duplicate task IDs')
    require(all(t['split'] in {'development','holdout'} for t in tasks), 'Unknown split')
    families = {s:{t['family'] for t in tasks if t['split']==s} for s in ('development','holdout')}
    require(not families['development'] & families['holdout'], 'Family leakage between splits')
    return value


def freeze(dataset, config, output, *, backend='native', evaluation_config=None, planning_policy='strict'):
    manifest = public_manifest(dataset)
    artifacts = Artifacts(output)
    require(backend in {'native','vitis'},'Unknown backend')
    require(planning_policy in {'strict','bounded-v2'}, 'Unknown planning policy')
    require(backend!='vitis' or evaluation_config is not None,'Vitis requires an evaluation config')
    protocol = dict(schema_version=1, dataset_id=manifest['dataset_id'], source_files=source_snapshot(), planning_policy=planning_policy,
                    runtime=load_config(config, allow_external=True), max_cases=4096, timeout=120,
                    fixture_manifest_sha256=digest((Path(dataset)/'fixtures.json').read_bytes()),
                    evaluation=dict(backend=backend,timeout=120,runtime=load_config(evaluation_config,frozen=True,allow_external=True) if evaluation_config else None),
                    max_requests_per_task=2, repeats=1, retries=0, seed_policy='record model-proposed test seed',
                    input_policy='Only problem and interface; no candidate/reference/recorded response',
                    holdout_policy='Unlock only after development evaluation; no changes or selective reruns')
    protocol['protocol_id'] = json_digest(protocol)
    artifacts.json('protocol.json', protocol)
    return protocol


def check_protocol(protocol, dataset):
    require(protocol['protocol_id']==json_digest({k:v for k,v in protocol.items() if k!='protocol_id'}), 'Protocol changed')
    require(source_snapshot()==protocol['source_files'], 'Code or prompts changed after freeze')
    manifest=public_manifest(dataset)
    require(manifest['dataset_id']==protocol['dataset_id'], 'Dataset differs from frozen protocol')
    return manifest


def generate(dataset, protocol_file, output, split, development_report=None, model=None):
    dataset=Path(dataset).resolve()
    protocol=read(protocol_file)
    manifest=check_protocol(protocol,dataset)
    if split=='holdout':
        require(development_report is not None, 'Holdout requires completed development evaluation')
        report=read(development_report)
        require(report['split']=='development' and report['protocol_id']==protocol['protocol_id']
                and report['complete'], 'Development report does not match protocol')
    artifacts=Artifacts(output)
    artifacts.json('protocol.json',protocol)
    rows=[]
    selected=[t for t in manifest['tasks'] if t['split']==split]
    require(bool(selected),'Empty split')
    for task in selected:
        check_protocol(protocol,dataset)
        directory=dataset/'public'/task['id']
        public=PublicTask.load(directory/'problem.txt',directory/'interface.h')
        require(all(public.snapshot()[k]==task[k] for k in public.snapshot()),'Public task changed')
        result=generate_suite(public,artifacts.root/'suites'/task['id'],model=model or ModelClient(),
                              runtime=protocol['runtime'],max_cases=protocol['max_cases'],timeout=protocol['timeout'],
                              planning_policy=protocol.get('planning_policy','strict'))
        rows.append(dict(id=task['id'],split=split,**result))
        artifacts.json('generation_summary.json',dict(protocol_id=protocol['protocol_id'],dataset_id=protocol['dataset_id'],
                       split=split,complete=len(rows)==len(selected),tasks=rows))
        print(task['id']+': '+result['status'],flush=True)
    check_protocol(protocol,dataset)
    return read(artifacts.root/'generation_summary.json')


def summarize(rows):
    total=len(rows)
    valid=[r for r in rows if r['generation_status']=='generated_unreviewed']
    complete=[r for r in valid if r.get('control_status') in {'selftest_passed','selftest_failed'}]
    accepted=[r for r in valid if r.get('control_status')=='selftest_passed']
    mutants=[v for r in accepted for v in r['variants'] if v['role']=='mutant']
    killed=sum(v['status']=='selftest_failed' for v in mutants)
    false=sum(r.get('control_status')=='selftest_failed' for r in rows)
    def rate(a,b): return a/b if b else None
    return dict(tasks=total,generated=len(valid),generation_failures=total-len(valid),
        generation_success_rate=rate(len(valid),total),correct_controls_passed=len(accepted),
        correct_control_pass_rate_all_tasks=rate(len(accepted),total),
        false_positives=false,false_positive_rate_completed_controls=rate(false,len(complete)),
        controls_not_evaluated=total-len(valid),control_inconclusive=len(valid)-len(complete),
        eligible_mutants=len(mutants),functional_kills=killed,mutation_detection_rate=rate(killed,len(mutants)),
        mutation_inconclusive=sum(v['status'] not in {'selftest_passed','selftest_failed'} for v in mutants),
        generation_requests=sum(r['generation_requests'] for r in rows))


def evaluate(dataset,generation,output,*,compiler='g++',allow_execution=False):
    dataset=Path(dataset).resolve(); generation=Path(generation).resolve()
    protocol=read(generation/'protocol.json')
    manifest=check_protocol(protocol,dataset)
    summary=read(generation/'generation_summary.json')
    require(summary['complete'] and summary['protocol_id']==protocol['protocol_id'],'Incomplete generation or protocol mismatch')
    selected=[t for t in manifest['tasks'] if t['split']==summary['split']]
    require([r['id'] for r in summary['tasks']]==[t['id'] for t in selected],'Missing/reordered generation tasks')
    # Pin every successful suite before accessing evaluation-only implementations.
    frozen={r['id']:load_suite(generation/'suites'/r['id'])[0]['suite_id'] for r in summary['tasks'] if r['status']=='generated_unreviewed'}
    require(digest((dataset/'fixtures.json').read_bytes())==protocol['fixture_manifest_sha256'],'Private fixture manifest changed after freeze')
    fixtures=read(dataset/'fixtures.json')
    require(fixtures['dataset_id']==protocol['dataset_id'],'Wrong private fixtures')
    private={t['id']:t for t in fixtures['tasks']}
    artifacts=Artifacts(output); artifacts.json('frozen_suites.json',frozen)
    rows=[]
    for generated in summary['tasks']:
        name=generated['id']
        row=dict(id=name,generation_status=generated['status'],generation_requests=generated['generation_requests'],
                 generation_seconds=generated['elapsed_seconds'],variants=[])
        if name in frozen:
            variants=private[name]['variants']
            require(sum(v['role']=='correct' for v in variants)==1,'Require exactly one correct control')
            for variant in variants:
                identifier(variant['id'])
                require(variant['role'] in {'correct','mutant'},'Unknown variant role')
                source=(dataset/variant['path']).resolve()
                require(source.is_relative_to(dataset) and digest(source.read_bytes())==variant['sha256'],'Private fixture changed')
                suite=generation/'suites'/name
                require(load_suite(suite)[0]['suite_id']==frozen[name],'Suite changed during evaluation')
                result=run_suite(suite,source,artifacts.root/name/variant['id'],compiler=compiler,allow_execution=allow_execution,
                                 backend=protocol['evaluation']['backend'],runtime=protocol['evaluation']['runtime'],timeout=protocol['evaluation']['timeout'])
                row['variants'].append(dict(id=variant['id'],role=variant['role'],status=result['status'],
                                           mismatch_count=result['mismatch_count'],first_failure=result['first_failure']))
                if variant['role']=='correct': row['control_status']=result['status']
        rows.append(row)
        artifacts.json('progress.json',rows)
    check_protocol(protocol,dataset)
    report=dict(protocol_id=protocol['protocol_id'],dataset_id=protocol['dataset_id'],split=summary['split'],
                complete=True,metrics=summarize(rows),tasks=rows,
                backend=protocol['evaluation']['backend'],
                limitation='Small synthetic family-separated pilot; C-level functional checks only; not official correctness or training-data exclusion.')
    artifacts.json('study_result.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('freeze'); p.add_argument('dataset');p.add_argument('config');p.add_argument('output')
    p.add_argument('--backend',choices=['native','vitis'],default='native');p.add_argument('--evaluation-config')
    p.add_argument('--planning-policy',choices=['strict','bounded-v2'],default='strict')
    p=sub.add_parser('generate');p.add_argument('dataset');p.add_argument('protocol');p.add_argument('output')
    p.add_argument('--split',choices=['development','holdout'],required=True);p.add_argument('--development-report')
    p=sub.add_parser('evaluate');p.add_argument('dataset');p.add_argument('generation');p.add_argument('output')
    p.add_argument('--compiler',default='g++');p.add_argument('--allow-execution',action='store_true')
    args=parser.parse_args()
    if args.command=='freeze': result=freeze(args.dataset,args.config,args.output,backend=args.backend,evaluation_config=args.evaluation_config,planning_policy=args.planning_policy)
    elif args.command=='generate': result=generate(args.dataset,args.protocol,args.output,args.split,args.development_report)
    else: result=evaluate(args.dataset,args.generation,args.output,compiler=args.compiler,allow_execution=args.allow_execution)
    print(json.dumps(result.get('metrics',{'complete':result.get('complete',True),'protocol_id':result['protocol_id']}),indent=2))
