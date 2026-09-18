import hashlib,json,shutil,tomllib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'output/frozen_baseline_pass5_20260915T214330'
DATA=ROOT.parent/'hls-overlap-audit/hls-eval/hls_eval_data'
OUT=ROOT/'output/baseline_pass5_validation_ready_20260915'
def read(p): return json.loads(p.read_text(encoding='utf-8'))
def good(x): return x and x.get('exit_code')==0 and (x.get('result') or {}).get('source')=='candidate.cpp'
def main():
 if OUT.exists(): raise FileExistsError(OUT)
 base=read(RUN/'manifest.json'); rec=read(RUN/'infrastructure_recovery/manifest.json')['results']; r2=RUN/'infrastructure_recovery_round2/tasks'
 selected={(x['task_id'],x['sample']):RUN/'tasks'/x['task_id']/f"sample_{x['sample']}"/'candidate.cpp' for x in base['results'] if good(x)}
 for x in rec:
  if good(x): selected[(x['task_id'],x['sample'])]=RUN/'infrastructure_recovery/tasks'/x['task_id']/f"sample_{x['sample']}"/'candidate.cpp'
 for task,sample in [('flowgnn/fgnn_linear_input_stationary',2),('pp4fpga/pp4fpga_cordic',3)]: selected[(task,sample)]=r2/task/f'sample_{sample}'/'candidate.cpp'
 OUT.mkdir(); records=[]
 for (task,sample),src in sorted(selected.items()):
  if not src.is_file(): continue
  d=DATA/task; dest=OUT/'inputs'/task/f'sample_{sample}'; dest.mkdir(parents=True)
  tb=next(d.glob('*_tb.cpp')); cfg=tomllib.loads((d/'hls_eval_config.toml').read_text())
  deps=[tb]+[p for p in d.iterdir() if p.suffix in {'.h','.hpp','.hh'}]+[d/n for n in cfg.get('tb_data',[])]
  shutil.copyfile(src,dest/src.name)
  for p in deps: shutil.copyfile(p,dest/p.name)
  records.append({'task_id':task,'sample':sample,'source':src.name,'testbench':tb.name,'top':(d/'top.txt').read_text().strip(),'tb_data':cfg.get('tb_data',[]),'source_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'dependencies':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in deps}})
 manifest={'task_count':base['task_count'],'samples':base['samples'],'candidates':len(records),'part':'xczu3eg-sbva484-1-e','clock_ns':5,'cxx_standard':'c++14','compile_timeout':180,'run_timeout':120,'synth_timeout':300,'source_mutations':False,'records':records}
 (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
 print(json.dumps({'output':str(OUT),'candidates':len(records)}))
if __name__=='__main__': main()
