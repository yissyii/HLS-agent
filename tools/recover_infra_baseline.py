import argparse, json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from serve.baseline_entry import run

def main():
 p=argparse.ArgumentParser(); p.add_argument('source'); p.add_argument('--dataset',required=True); p.add_argument('--workers',type=int,default=2); a=p.parse_args()
 source=Path(a.source).resolve(); out=source/'infrastructure_recovery'; out.mkdir(exist_ok=False)
 m=json.loads((source/'manifest.json').read_text(encoding='utf-8')); cfg=m['config']; data=Path(a.dataset).resolve()
 jobs=[x for x in m['results'] if (x.get('result') or {}).get('category') in {'api_network_or_timeout','api_http_error'}]
 result={'source_manifest':'manifest.json','mode':'infrastructure_recovery_same_problem_same_config','count':len(jobs),'results':[]}
 def one(x):
  task,sample=x['task_id'],x['sample']; dest=out/'tasks'/task/f'sample_{sample}'
  try:
   code,receipt=run(data/task/'kernel_description.md',dest,cfg,f'{task}/sample_{sample}/recovery')
   return {'task_id':task,'sample':sample,'exit_code':code,'result':receipt}
  except Exception as e:return {'task_id':task,'sample':sample,'exit_code':1,'error':f'{type(e).__name__}: {e}'}
 with ThreadPoolExecutor(max_workers=a.workers) as pool:
  for f in as_completed([pool.submit(one,x) for x in jobs]):
   result['results'].append(f.result()); (out/'manifest.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
 result['completed']=True; result['generated']=sum(x['exit_code']==0 for x in result['results']); (out/'manifest.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
if __name__=='__main__': main()
