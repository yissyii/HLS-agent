"""Isolated new version: replay old logs and rerun two real HLS failures, no model."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import time
from dataclasses import asdict

base = Path('/home/dingjy/sxt/zcomp-agent')
old = base / 'formal-eval-fine-diag-20260923-v2'
new = base / 'formal-eval-fine-diag-20260923-v3-linker'
patch_dir = Path(__file__).resolve().parent
assert not new.exists(), 'Use a fresh version; never overwrite evidence'
shutil.copytree(old, new, ignore=shutil.ignore_patterns('output', '.git', '__pycache__', '*.pyc', '.venv'))
for name, target in [('diagnostics.py', 'evaluation/diagnostics.py'),
                     ('test_linker_diagnostics.py', 'tools/test_linker_diagnostics.py')]:
    shutil.copy2(patch_dir / name, new / target)
out = new / 'output/linker_verification'
out.mkdir(parents=True)
sys.path.insert(0, str(new))
from evaluation.diagnostics import extract_diagnostics
from evaluation.functional import format_functional
from evaluation.validator import HLSValidator
from evaluation.task_io import load_task
from agent.core.contracts import Candidate
from agent.core.policy import load_policy
from agent.context.builder import build
from agent.feedback.diagnostics import classify
from serve.inference import load_config

report = dict(version=str(new), old_evidence_untouched=True, model_requests=0,
              parser_sha256=hashlib.sha256((new/'evaluation/diagnostics.py').read_bytes()).hexdigest(),
              replay=[], actual=[])
sp = sorted((old/'output/local_eval').glob('*/attempt_*/artifacts/compare_*/summary.json'))[-1]
original_summary_hash = hashlib.sha256(sp.read_bytes()).hexdigest()
for p in sorted(sp.parent.glob('Prob*/agent/candidates/*/prompt.txt')):
    match = re.search(r'<DIAGNOSTIC[^>]*category="([^"]+)"[^>]*>(.*?)</DIAGNOSTIC>', p.read_text(), re.S)
    if not match:
        continue
    category, previous_feedback = match.groups()
    previous = p.parent.parent / ('%03d' % (int(p.parent.name)-1)) / 'work'
    stage = 'synthesis' if category == 'synthesis_error' else 'csim'
    log = previous / (stage+'.log')
    result = extract_diagnostics(stage, dict(timed_out=False, exit_code=1), log.read_text(errors='replace'))
    feedback = (format_functional(result['functional_diagnostics']) if
                category == 'functional_or_runtime_error' else result['compiler_text'])
    report['replay'].append(dict(prompt=str(p), category=category, detailed=bool(feedback),
                                 previous_bare=previous_feedback.strip()==category))
assert len(report['replay']) == 91
assert all(r['detailed'] for r in report['replay']), 'Remaining coverage gap'
runtime = load_config(new/'serve/runtime.formal.json')
policy = load_policy(new/'agent/config/policy.json')
for task_id in ('Prob078', 'Prob110'):
    manifest = new/'data/processed/bench4hls'/task_id/'task.json'
    task = load_task(manifest.parent/'problem.txt', manifest)
    original = sp.parent/task_id/'agent/candidates/000/candidate.cpp'
    candidate_path = out/task_id/'candidates/000/candidate.cpp'
    candidate_path.parent.mkdir(parents=True)
    shutil.copy2(original, candidate_path)
    candidate = Candidate(0, original.read_text(), candidate_path, None)
    validation = HLSValidator(True).check(candidate, task, runtime, 'csim', time.monotonic()+180)
    prompt = build(task, runtime, policy, candidate, classify(validation))
    assert validation.outcome['category'] == 'compile_error'
    assert 'undefined symbol: TopModule(' in validation.feedback_text
    assert 'referenced by tb.cpp:' in prompt.text
    (candidate_path.parent/'repair_prompt_verified.txt').write_text(prompt.text)
    report['actual'].append(dict(task=task_id, validation=asdict(validation), prompt=prompt.text))
    print(task_id, 'actual HLS -> detailed linker feedback -> repair prompt verified', flush=True)
assert hashlib.sha256(sp.read_bytes()).hexdigest() == original_summary_hash
(out/'report.json').write_text(json.dumps(report, indent=2))
print(json.dumps(dict(replayed=len(report['replay']), detailed=sum(r['detailed'] for r in report['replay']),
                      actual_hls_verified=len(report['actual']), model_requests=0, report=str(out/'report.json'))))
