"""Prepare isolated remote fine-feedback rerun; preserve all old evidence."""
from pathlib import Path
import shutil
import json
import hashlib

base = Path('/home/dingjy/sxt/zcomp-agent')
old = base / 'formal-eval-functional-diag-20260922'
new = base / 'formal-eval-fine-diag-20260923-v2'
assert old.is_dir() and not new.exists(), 'Require fresh destination'
shutil.copytree(old, new, ignore=shutil.ignore_patterns('output', '.git', '__pycache__', '*.pyc', '.venv', 'node_modules'))
p = new / 'evaluation/hls.py'
s = p.read_text()
needle = '        execution["category"] = classify(stage, execution, text)\n        execution["diagnostic_tail"] = "\\n".join(text.splitlines()[-20:])'
assert s.count(needle) == 1
s = 'from evaluation.diagnostics import extract_diagnostics\n' + s.replace(needle, '''        extracted = extract_diagnostics(stage, execution, text)
        execution["category"] = extracted["category"]
        execution["diagnostics"] = extracted["entries"]
        execution["compiler_text"] = extracted["compiler_text"]
        execution["functional_diagnostics"] = extracted["functional_diagnostics"]
        execution["diagnostic_tail"] = extracted["diagnostic_tail"]''')
p.write_text(s)
manifests = list((new / 'data/processed/bench4hls').glob('*/task.json'))
assert len(manifests) == 170
assert all(json.loads(p.read_text())['feedback_policy'] == 'functional_diagnostics' for p in manifests)
policy = json.loads((new / 'agent/config/policy.json').read_text())
assert not policy['rag_enabled'] and not policy['skills_enabled']
proof = dict(experiment='fine_feedback_v2', old_experiment_is_coarse=str(old), tasks=170, policy=policy,
    hashes={str(p.relative_to(new)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ('evaluation','agent','serve','local_eval','tools') for p in (new/folder).rglob('*.py')})
(new / 'fine_rerun_manifest.json').write_text(json.dumps(proof, indent=2))
(new / 'smoke_inputs').mkdir()
for task in ('Prob006','Prob030'):
    sources = sorted((old / 'output/local_eval').glob('*/attempt_*/artifacts/compare_*/'+task+'/agent/candidates/000/candidate.cpp'))
    assert sources, task
    shutil.copy2(sources[0], new / 'smoke_inputs' / (task+'.cpp'))
print(new)
