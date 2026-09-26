"""Native C++ evidence for the prospective public-behavior stress suite."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.bench4hls_behavior_stress_cases import cpp_source, reference_testbench, mutation_names
from tools.run_bench4hls_behavior_stress import verify_suite
from tools.bench4hls_native_worker import _safe_file, _job_id


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    return sha(Path(path).read_bytes())


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes((json.dumps(value, indent=2)+'\n').encode('utf8'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_new(path):
    require(not Path(path).exists(), 'Output already exists: '+str(path))


def new_package(output):
    output = Path(output).resolve()
    check_new(output)
    check_new(output.with_suffix('.tar.gz'))
    output.mkdir()
    for name in ('sources','tests','tool_sources'):
        (output/name).mkdir()
    return output


def snapshot_tools(output):
    hashes = {}
    for name in ('bench4hls_native_worker.py','bench4hls_behavior_stress_cases.py','bench4hls_behavior_stress_native.py'):
        target = output/'tool_sources'/name
        shutil.copyfile(ROOT/'tools'/name, target)
        hashes['tool_sources/'+name] = file_sha(target)
    shutil.copyfile(ROOT/'tools/bench4hls_native_worker.py', output/'worker.py')
    return hashes


def emit(output, jobs, manifest):
    ids = [job['id'] for job in jobs]
    require(len(ids)==len(set(ids)), 'Duplicate job ids')
    for job in jobs:
        _job_id(job['id'])
    write(output/'jobs.json', {'jobs':jobs})
    manifest.update(jobs_sha256=file_sha(output/'jobs.json'),
                    tool_source_hashes=snapshot_tools(output),
                    worker_sha256=file_sha(output/'worker.py'))
    write(output/'package_manifest.json', manifest)
    with tarfile.open(output.with_suffix('.tar.gz'), 'w:gz') as archive:
        archive.add(output, arcname=output.name)


def job_fields(job):
    return {k:job[k] for k in ('id','source','testbench','source_sha256','testbench_sha256')}


def package_errors(package, manifest, entries):
    errors = []
    if file_sha(package/'jobs.json') != manifest.get('jobs_sha256'):
        errors.append('jobs_hash')
    raw = read(package/'jobs.json')
    if raw != {'jobs':[job_fields(e) for e in entries]}:
        errors.append('job_manifest_mismatch')
    if file_sha(package/'worker.py') != manifest.get('worker_sha256'):
        errors.append('worker_hash')
    for path, digest in manifest.get('tool_source_hashes',{}).items():
        if file_sha(_safe_file(package,path,'tool source')) != digest:
            errors.append('tool_source_hash:'+path)
    ids = [e['id'] for e in entries]
    if len(ids)!=len(set(ids)):
        errors.append('duplicate_jobs')
    for entry in entries:
        _job_id(entry['id'])
        for field in ('source','testbench'):
            if file_sha(_safe_file(package,entry[field],field))!=entry[field+'_sha256']:
                errors.append('local_hash:'+entry['id']+':'+field)
    return errors


def result_index(package, entries):
    results = read(package/'results.json')['jobs']
    by_id = {r.get('id'):r for r in results}
    errors = []
    if len(by_id)!=len(results) or set(by_id)!={e['id'] for e in entries}:
        errors.append('result_id_set')
    for entry in entries:
        r = by_id.get(entry['id'],{})
        if not r.get('hash_verified') or any(r.get('hashes',{}).get(k)!=entry[k+'_sha256'] for k in ('source','testbench')):
            errors.append('result_hash:'+entry['id'])
    return by_id, errors


def run_status(result):
    compile_result, run = result.get('compile',{}), result.get('run',{})
    if compile_result.get('timeout') or run.get('timeout'):
        return 'timeout'
    if compile_result.get('returncode') != 0:
        return 'compile_error'
    if run.get('returncode') == 0:
        return 'passed'
    if run.get('returncode') == 1:
        return 'assertion_failed'
    return 'run_error'


def prepare_controls(suite, output):
    suite = Path(suite).resolve()
    sm = verify_suite(suite)
    output = new_package(output)
    cases = {c['case_id']:c for c in read(suite/'reference/cases.json')}
    jobs = []
    for item in sm['cases']:
        case = cases[item['case_id']]
        for mutant in [None]+mutation_names(case):
            source, bench = cpp_source(case,mutant), reference_testbench(case)
            if source is None:
                continue
            stem = case['case_id']+'_'+(mutant or 'correct')
            source_path = 'sources/'+stem+'.cpp'
            (output/source_path).write_bytes(source.encode('utf8'))
            roles = [('correct/reference',0,None)] if mutant is None else [
                ('mutant/reference',1,None),('mutant/mutant-self-reference',0,mutant)]
            for role, returncode, reference_mutant in roles:
                bench = reference_testbench(case, reference_mutant)
                test_path = 'tests/'+stem+'_'+role.replace('/','_')+'.cpp'
                (output/test_path).write_bytes(bench.encode('utf8'))
                jobs.append(dict(id=stem+'_'+role.replace('/','_'), role=role, case_id=case['case_id'],
                                 variant=mutant or 'correct', expected_returncode=returncode,
                                 source=source_path, testbench=test_path,
                                 source_sha256=file_sha(output/source_path), testbench_sha256=file_sha(output/test_path)))
    manifest = dict(version=2, suite_manifest_sha256=file_sha(suite/'manifest.json'),
                    fixture_source_sha256=file_sha(ROOT/'tools/bench4hls_behavior_stress_cases.py'),jobs=jobs)
    emit(output,[job_fields(e) for e in jobs],manifest)
    return manifest


def assess_controls(package):
    m = read(package/'package_manifest.json')
    errors = package_errors(package,m,m['jobs'])
    results, result_errors = result_index(package,m['jobs'])
    errors += result_errors
    cases = {}
    for entry in m['jobs']:
        status = run_status(results.get(entry['id'],{}))
        expected = 'passed' if entry['expected_returncode']==0 else 'assertion_failed'
        passed = status==expected
        if not passed:
            errors.append('job:'+entry['id']+':'+status)
        cases.setdefault(entry['case_id'],[]).append(dict(entry, passed=passed))
    return dict(status='qualified' if not errors else 'failed',errors=errors,
                package_manifest_sha256=file_sha(package/'package_manifest.json'),
                results_sha256=file_sha(package/'results.json'),cases=cases)


def qualify(package, output):
    package, output = Path(package).resolve(), Path(output).resolve()
    check_new(output)
    result = assess_controls(package)
    write(output,result)
    return result


def prepare_pilot(suite, controls, runs, output):
    suite, controls = Path(suite).resolve(), Path(controls).resolve()
    sm = verify_suite(suite)
    suite_hash = file_sha(suite/'manifest.json')
    q = read(controls/'control_qualification.json')
    require(q==assess_controls(controls) and q['status']=='qualified', 'controls_not_qualified_or_changed')
    cm = read(controls/'package_manifest.json')
    require(cm['suite_manifest_sha256']==suite_hash, 'control_suite_mismatch')
    output = new_package(output)
    jobs, plans, run_entries = [], [], []
    for run in map(lambda p:Path(p).resolve(),runs):
        rm, summary, audit = read(run/'manifest.json'), read(run/'summary.json'), read(run/'audit_v1.json')
        require(summary['status']=='completed' and rm['suite_manifest_sha256']==suite_hash
                and audit['run_manifest_sha256']==file_sha(run/'manifest.json')
                and audit['suite_manifest_sha256']==suite_hash and not audit['report_integrity_errors'], 'invalid_run')
        planned = {(c['case_id'],i) for c in sm['cases'] for i in range(1,rm['repeats']+1)}
        actual = {(r['case_id'],r['run_index']) for r in audit['rows']}
        require(actual==planned and len(audit['rows'])==len(planned), 'audit_denominator_mismatch')
        require({(r['task_id'],r['run_index']) for r in summary['attempts']}==planned
                and len(summary['attempts'])==len(planned), 'summary_denominator_mismatch')
        run_key = rm['representation']+'_'+sha(str(run).encode())[:12]
        run_entries.append(dict(run=str(run),representation=rm['representation'],
                                manifest_sha256=file_sha(run/'manifest.json'),audit_sha256=file_sha(run/'audit_v1.json'),
                                summary_sha256=file_sha(run/'summary.json')))
        for row in audit['rows']:
            key = run_key+'_'+row['case_id']+'_'+str(row['run_index'])
            cpp = run/row['case_id']/('run_%03d'%row['run_index'])/'generation/selftest.cpp'
            plan = dict(key=key,run=str(run),representation=rm['representation'],case_id=row['case_id'],
                        run_index=row['run_index'],audit=row,jobs=[])
            controls_case = q['cases'].get(row['case_id'],[])
            if cpp.is_file():
                require(row['selftest_sha']==file_sha(cpp), 'testbench_changed_since_audit')
            if cpp.is_file() and not row['errors'] and controls_case:
                test_path = 'tests/'+key+'_generated.cpp'
                shutil.copyfile(cpp,output/test_path)
                for control in controls_case:
                    if control['role'] not in ('correct/reference','mutant/reference'):
                        continue
                    source_path = 'sources/'+control['id']+'.cpp'
                    shutil.copyfile(controls/control['source'],output/source_path)
                    require(file_sha(output/source_path)==control['source_sha256'], 'control_source_changed')
                    entry = dict(id=key+'_'+control['id'],source=source_path,testbench=test_path,
                                 source_sha256=control['source_sha256'],testbench_sha256=file_sha(output/test_path),
                                 role=control['role'],mutation=None if control['variant']=='correct' else control['variant'])
                    jobs.append(job_fields(entry))
                    plan['jobs'].append(entry)
            elif cpp.is_file():
                plan['no_independent_native_control']=True
            plans.append(plan)
    manifest = dict(version=2,suite_manifest_sha256=suite_hash,runs=run_entries,
                    controls_package_manifest_sha256=file_sha(controls/'package_manifest.json'),
                    controls_qualification_sha256=file_sha(controls/'control_qualification.json'),plans=plans)
    emit(output,jobs,manifest)
    return manifest


def aggregate(rows):
    supported = [r for r in rows if r['audit']['expected_scope']=='supported']
    groups = {}
    for row in supported:
        groups.setdefault((row['run'],row['case_id']),[]).append(row)
    return dict(attempts=len(rows),supported_attempts=len(supported),
                bounded_native_qualified=sum(r['bounded_native_qualified'] for r in supported),
                stable_supported_cases=sum(all(r['bounded_native_qualified'] for r in group) for group in groups.values()),
                supported_cases=len(groups),
                generated=sum(r['audit']['generation_status']=='generated_unreviewed' for r in rows),
                negative_explicit_abstained=sum(r['audit']['expected_scope']!='supported' and r['audit']['generation_status']=='abstained' and not r['audit']['errors'] for r in rows),
                generated_on_out_of_scope=sum(r['audit']['expected_scope']!='supported' and r['audit']['generation_status']=='generated_unreviewed' for r in rows),
                mutation_opportunities=sum(len(r['native_mutants']) for r in supported),
                mutant_statuses=dict(Counter(v for r in rows for v in r['native_mutants'].values())),
                native_correct_failures=sum(r['native_correct_status']=='assertion_failed' for r in rows),
                model_requests=sum(r['audit']['model_requests'] for r in rows),
                total_tokens_reported=sum(r['audit']['total_tokens_reported'] for r in rows))


def summarize(package, output):
    package, output = Path(package).resolve(), Path(output).resolve()
    check_new(output)
    m = read(package/'package_manifest.json')
    entries = [j for p in m['plans'] for j in p['jobs']]
    errors = package_errors(package,m,entries)
    results, result_errors = result_index(package,entries)
    errors += result_errors
    rows=[]
    for plan in m['plans']:
        a = plan['audit']
        correct = next((j for j in plan['jobs'] if j['mutation'] is None), None)
        correct_status = run_status(results.get(correct['id'],{})) if correct else 'not_run'
        cp = correct_status=='passed' and not errors
        mutants={name:'not_run' for name in a['mutants']}
        for entry in plan['jobs']:
            if entry['mutation'] is None:
                continue
            status = run_status(results.get(entry['id'],{}))
            if errors:
                value='integrity_error'
            elif not cp or a['errors'] or any(a[k]['status'] in ('counterexample','audit_error','invalid_declaration') for k in ('primary_audit','vector_audit')):
                value='invalid_test'
            elif status=='assertion_failed':
                value='killed'
            elif status=='passed':
                value='survived'
            else:
                value=status
            mutants[entry['mutation']]=value
        rows.append(dict(case_id=plan['case_id'],run_index=plan['run_index'],run=plan['run'],
                         representation=plan['representation'],bounded_python_qualified=a['bounded_python_qualified'],
                         native_correct_status=correct_status,native_correct_pass=cp,
                         bounded_native_qualified=bool(a['bounded_python_qualified'] and cp),native_mutants=mutants,audit=a))
    report = dict(result_integrity_errors=errors,package_manifest_sha256=file_sha(package/'package_manifest.json'),
                  results_sha256=file_sha(package/'results.json'),evaluator_sha256=file_sha(Path(__file__)),
                  rows=rows,overall=aggregate(rows),by_representation={},by_case={})
    for representation in sorted({r['representation'] for r in rows}):
        report['by_representation'][representation]=aggregate([r for r in rows if r['representation']==representation])
        report['by_case'][representation]={name:aggregate([r for r in rows if r['representation']==representation and r['case_id']==name]) for name in sorted({r['case_id'] for r in rows})}
    write(output,report)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('prepare-controls','qualify','prepare-pilot','summarize'):
        sub.add_parser(name).add_argument('--output',required=True,type=Path)
    sub.choices['prepare-controls'].add_argument('--suite',required=True,type=Path)
    sub.choices['qualify'].add_argument('--package',required=True,type=Path)
    sub.choices['prepare-pilot'].add_argument('--suite',required=True,type=Path)
    sub.choices['prepare-pilot'].add_argument('--controls',required=True,type=Path)
    sub.choices['prepare-pilot'].add_argument('--run',required=True,type=Path,action='append')
    sub.choices['summarize'].add_argument('--package',required=True,type=Path)
    args=parser.parse_args(argv)
    if args.command=='prepare-controls': prepare_controls(args.suite,args.output)
    elif args.command=='qualify': qualify(args.package,args.output)
    elif args.command=='prepare-pilot': prepare_pilot(args.suite,args.controls,args.run,args.output)
    else: summarize(args.package,args.output)

if __name__=='__main__': main()
