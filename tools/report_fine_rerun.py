import json, collections, statistics
from pathlib import Path
import sys, time, re
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from evaluation.metrics import eligible, summarize
while True:
    matches=sorted((root/'output/local_eval').glob('*/attempt_*/artifacts/compare_*/summary.json'))
    if matches:
        sp=matches[-1]
        try:
            s=json.loads(sp.read_text())
            if s['status']=='completed': break
        except (ValueError, OSError): pass
    time.sleep(20)
assert len(s['results'])==340
assert len({(r['task'],r['method']) for r in s['results']})==340
def read(p): return json.loads(p.read_text())
records={}
stats={}
sources=collections.Counter()
for row in s['results']:
    origin=Path(row.get('origin_summary',str(sp)))
    sources[str(origin)]+=1
    d=origin.parent/row['task']/('baseline_gen' if row['method']=='baseline' else 'agent')
    receipt=read(d/'result.json') if (d/'result.json').exists() else {}
    checks=row.get('checks') or {}
    cause=('passed' if row.get('overall') else
           'compile_error' if checks.get('compile')=='failed' else
           'functional_or_runtime_error' if checks.get('run')=='failed' else
           'synthesis_error' if checks.get('synthesize')=='failed' else
           receipt.get('category') or row.get('reason') or row.get('stop_reason') or 'unclassified')
    candidates=receipt.get('candidates',[])
    first=candidates[0].get('checks',{}) if candidates else {}
    firstpass=first.get('run')==first.get('synthesize')=='passed'
    firstmeta=d/'candidates/000/response.txt.meta.json'
    metas=[read(p) for p in d.glob('candidates/*/response.txt.meta.json')] if row['method']=='agent' else [read(p) for p in d.glob('response.txt.meta.json')]
    record=dict(task=row['task'],method=row['method'],overall=bool(row.get('overall')),
        checks=checks,cause=cause,stop=receipt.get('stop_reason') or receipt.get('category'),
        requests=sum(m.get('requests',0) for m in metas),
        transport_retries=sum(m.get('transport_retries',0) for m in metas),
        logical_requests=receipt.get('generation_requests'),
        repairs=receipt.get('repair_attempts_used',0),
        first_pass=firstpass if row['method']=='agent' else None,
        elapsed=receipt.get('elapsed_seconds'), origin=str(d),
        feedback_events=sum(len(v.get('outcome',{}).get('functional_diagnostics',{}).get('events',[])) for v in receipt.get('validation_history',[])),
        functional_prompts=sum('Functional diagnostic (observed facts;' in p.read_text() for p in d.glob('candidates/*/prompt.txt')),
        retry_attempts=[a for m in metas for a in m.get('transport_attempts',[]) if a.get('category')],
        )
    records[(row['task'],row['method'])]=record
for m in ['baseline','agent']:
    rr=[r for (t,mm),r in records.items() if mm==m]
    stats[m]=dict(n=len(rr),stages={k:sum(r['checks'].get(k)=='passed' for r in rr) for k in ['parse','compile','run','synthesize']},
        passed=sum(r['overall'] for r in rr),causes=dict(collections.Counter(r['cause'] for r in rr)),
        stops=dict(collections.Counter(r['stop'] for r in rr)),
        requests=sum(r['requests'] for r in rr),transport_retries=sum(r['transport_retries'] for r in rr),
        mean_generation_or_agent_elapsed=statistics.mean(r['elapsed'] for r in rr if r['elapsed'] is not None),
        request_distribution=dict(collections.Counter(r['logical_requests'] for r in rr)),
        repair_distribution=dict(collections.Counter(r['repairs'] for r in rr)),
        first_pass=sum(r['first_pass'] is True for r in rr),
        repaired_pass=[r['task'] for r in rr if r['first_pass'] is False and r['overall']],
        lost_pass=[r['task'] for r in rr if r['first_pass'] is True and not r['overall']],
        functional_feedback_tasks=[r['task'] for r in rr if r['functional_prompts']],
        functional_feedback_prompts=sum(r['functional_prompts'] for r in rr),
        retry_tasks=[dict(task=r['task'],retries=r['transport_retries'],attempts=r['retry_attempts']) for r in rr if r['transport_retries']])
groups={k:[] for k in ['both_pass','agent_only','baseline_only','both_fail']}
paired=[]
for t in sorted({t for t,m in records}):
    b,a=records[t,'baseline'],records[t,'agent']
    group='both_pass' if b['overall'] and a['overall'] else 'baseline_only' if b['overall'] else 'agent_only' if a['overall'] else 'both_fail'
    groups[group].append(t)
    paired.append(dict(task=t,group=group,baseline=b['cause'],agent=a['cause'],agent_stop=a['stop'],agent_first_pass=a['first_pass'],agent_repairs=a['repairs'],baseline_requests=b['requests'],agent_requests=a['requests'],baseline_origin=b['origin'],agent_origin=a['origin']))

feedback=[]
for p in sp.parent.glob('Prob*/agent/candidates/*/prompt.txt'):
    match=re.search(r'<DIAGNOSTIC[^>]*>(.*?)</DIAGNOSTIC>',p.read_text(),re.S)
    if not match: continue
    body=match.group(1).strip()
    feedback.append(dict(path=str(p.relative_to(root)), bare_category=body in {'compile_error','functional_or_runtime_error','synthesis_error','compile_or_csim_error'}, functional='Functional diagnostic (observed facts;' in body, diagnostic=body))
report=dict(summary=str(sp),experiment='fine_feedback_v2',old_results_reused=False,
    ability_metrics=summarize(s['results']),stats=stats,groups=groups,rows=paired,
    feedback_audit=dict(total=len(feedback),bare=sum(e['bare_category'] for e in feedback),detailed=sum(not e['bare_category'] for e in feedback),evidence=feedback),
    pending=[r for r in s['results'] if not eligible(r)],
    elapsed_seconds=s['elapsed_seconds'])
out=root/'output/fine_detailed_report.json'
out.write_text(json.dumps(report,indent=2,ensure_ascii=False))
print(str(out),flush=True)
