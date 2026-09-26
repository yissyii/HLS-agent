"""Freeze, generate, and independently audit a small public-behavior stress suite.

Private reference cases never enter the generation call: only each saved public
problem is supplied to the existing public-text-only generator.
"""
from __future__ import annotations
import argparse, hashlib, json, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import run_bench4hls_obligations_pilot as pilot
from tools.bench4hls_behavior_stress_cases import build_cases
from tools.bench4hls_behavior_stress_audit import audit_run

CONFIG = ROOT/'output/bench4hls_semantic_frozen20_20260925_v2/Prob004/frozen/config.json'
EXTRA_SOURCES = ('tools/bench4hls_behavior_stress_cases.py', 'tools/run_bench4hls_behavior_stress.py',
                 'tools/bench4hls_behavior_stress_audit.py', 'tools/audit_bench4hls_obligations_pilot.py')

def sha(value): return hashlib.sha256(value).hexdigest()
def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
def safe_id(value):
    return isinstance(value,str) and value and value.isascii() and all(c.isalnum() or c in '_-' for c in value)
def sources():
    base=pilot.source_hashes()
    for name in EXTRA_SOURCES:
        p=ROOT/name
        if not p.is_file(): raise ValueError('Missing stress source: '+name)
        base[name]=sha(p.read_bytes())
    return base
def verify_sources(expected):
    for name, digest in expected.items():
        p=ROOT/name
        if not p.is_file() or sha(p.read_bytes()) != digest: raise ValueError('source_mismatch:'+name)
def verify_suite(suite):
    manifest=read(suite/'manifest.json')
    verify_sources(manifest['source_hashes'])
    for name, digest in manifest['source_hashes'].items():
        snap=suite/'sources'/name
        if not snap.is_file() or sha(snap.read_bytes()) != digest: raise ValueError('source_snapshot_mismatch:'+name)
    cases=suite/'reference/cases.json'
    if sha(cases.read_bytes()) != manifest['cases_sha256']: raise ValueError('reference_hash_mismatch')
    private = read(cases)
    by_id = {c['case_id']:c for c in private}
    ids = [c['case_id'] for c in manifest['cases']]
    if len(ids) != 18 or len(set(ids)) != 18 or len(private) != 18 or set(by_id) != set(ids):
        raise ValueError('case_set_mismatch')
    config=Path(manifest['config_path'])
    if not config.is_file() or sha(config.read_bytes()) != manifest['config_sha256']: raise ValueError('config_mismatch')
    for row in manifest['cases']:
        if not safe_id(row['case_id']): raise ValueError('unsafe_case_id')
        raw=(suite/'public'/(row['case_id']+'.txt')).read_bytes()
        if sha(raw)!=row['problem_sha256']: raise ValueError('problem_mismatch:'+row['case_id'])
        reference = by_id[row['case_id']]
        if raw != reference['public_problem'].encode('utf8') or any(reference[k] != row[k] for k in ('family','expected_scope')):
            raise ValueError('public_reference_mismatch:'+row['case_id'])
    return manifest

def freeze(output, seed=20261002):
    if type(seed) is not int: raise ValueError('seed must be an integer')
    output=Path(output).resolve()
    if output.exists(): raise ValueError('Output already exists: '+str(output))
    cases=build_cases(seed)
    if len(cases)!=18 or len({c['case_id'] for c in cases})!=18 or not all(safe_id(c['case_id']) for c in cases): raise ValueError('Invalid fixture case IDs')
    src=sources()
    output.mkdir(); (output/'public').mkdir(); (output/'reference').mkdir(); (output/'sources').mkdir()
    for case in cases: (output/'public'/(case['case_id']+'.txt')).write_bytes(case['public_problem'].encode('utf-8'))
    write(output/'reference/cases.json', cases)
    for name, digest in src.items():
        target=output/'sources'/name; target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(ROOT/name,target)
        if sha(target.read_bytes()) != digest: raise ValueError('source_snapshot_copy_mismatch:'+name)
    manifest={'version':1,'seed':seed,'config_path':str(CONFIG),'config_sha256':sha(CONFIG.read_bytes()),'source_hashes':src,
              'cases_sha256':sha((output/'reference/cases.json').read_bytes()),
              'cases':[{'case_id':c['case_id'],'family':c['family'],'expected_scope':c['expected_scope'],
                        'problem_sha256':sha(c['public_problem'].encode())} for c in cases]}
    write(output/'manifest.json',manifest); verify_suite(output); return manifest

def generate(suite, output, representation='behaviors', repeats=2, max_calls=256, seed=20261003, **kwargs):
    if representation not in ('rules','behaviors'): raise ValueError('representation must be rules or behaviors')
    if type(repeats) is not int or type(max_calls) is not int or not 1 <= repeats <= 5 or not 1 <= max_calls <= 1024: raise ValueError('Invalid repeats or max_calls')
    if type(seed) is not int: raise ValueError('seed must be an integer')
    suite,output=Path(suite).resolve(),Path(output).resolve()
    if output.exists(): raise ValueError('Output already exists')
    manifest=verify_suite(suite); output.mkdir()
    run={'version':1,'suite_manifest_sha256':sha((suite/'manifest.json').read_bytes()),'representation':representation,'repeats':repeats,'max_calls':max_calls,'seed':seed}
    write(output/'manifest.json',run)
    summary={'status':'running','total':len(manifest['cases'])*repeats,'finished':0,'attempts':[], 'model_requests':0,'total_tokens_reported':0,'usage_complete':True}
    private = {c['case_id']:c for c in read(suite/'reference/cases.json')}
    write(output/'summary.json',summary)
    for row in manifest['cases']:
        task={'task_id':row['case_id'],'corpus_problem_path':'public/'+row['case_id']+'.txt','problem_sha256':row['problem_sha256'],'top':private[row['case_id']]['top']}
        for index in range(1,repeats+1):
            result=None
            try:
                verify_suite(suite)
                result=pilot.execute(suite,output,task,index,Path(manifest['config_path']),manifest['config_sha256'],max_calls,seed,representation,manifest['source_hashes'],**kwargs)
                summary_row=pilot.summary_row(result)
                try: verify_suite(suite)
                except Exception as exc: summary_row.update(integrity_error=str(exc), status='failed')
            except Exception as exc:
                if result is None:
                    result = {'task_id':row['case_id'],'run_index':index,'repeat_seed':seed+index-1,
                              'representation':representation,'status':'failed','generation_status':'not_run',
                              'failure_category':'integrity_error','failure_message':str(exc),
                              'model_requests':0,'total_tokens_reported':0,'usage_complete':False}
                    write(output/row['case_id']/('run_%03d'%index)/'result.json',result)
                summary_row=pilot.summary_row(result)
                summary_row.update(integrity_error=str(exc),status='failed')
            summary['attempts'].append(summary_row); summary['finished']+=1; summary['model_requests']+=summary_row.get('model_requests',0) or 0; summary['total_tokens_reported']+=summary_row.get('total_tokens_reported',0) or 0; summary['usage_complete'] &= bool(summary_row.get('usage_complete',False)); write(output/'summary.json',summary)
            print(json.dumps(summary_row), flush=True)
    summary['status']='completed'; summary['receipt_id']=sha(json.dumps(summary,sort_keys=True,separators=(',',':')).encode()); write(output/'summary.json',summary); return summary

def audit(suite, run, output):
    suite, run, output = Path(suite).resolve(), Path(run).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Audit output already exists')
    report = audit_run(suite, run, verify_suite(suite))
    write(output, report)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='command',required=True)
    f=sub.add_parser('freeze'); f.add_argument('--output',type=Path,required=True); f.add_argument('--seed',type=int,default=20261002)
    g=sub.add_parser('generate'); g.add_argument('--suite',type=Path,required=True); g.add_argument('--output',type=Path,required=True); g.add_argument('--representation',choices=('rules','behaviors'),required=True); g.add_argument('--repeats',type=int,default=2); g.add_argument('--max-calls',type=int,default=256); g.add_argument('--seed',type=int,default=20261003)
    a=sub.add_parser('audit'); a.add_argument('--suite',type=Path,required=True); a.add_argument('--run',type=Path,required=True); a.add_argument('--output',type=Path,required=True)
    x=p.parse_args(argv)
    if x.command=='freeze': freeze(x.output,x.seed)
    elif x.command=='generate': generate(x.suite,x.output,x.representation,x.repeats,x.max_calls,x.seed)
    else: audit(x.suite,x.run,x.output)
if __name__=='__main__': main()
