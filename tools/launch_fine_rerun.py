from pathlib import Path
import json
import re
import subprocess
import hashlib
import sys

root = Path('/home/dingjy/sxt/zcomp-agent/formal-eval-fine-diag-20260923-v2')
evidence = []
for p in (root / 'output/local_eval').glob('*/attempt_*/artifacts/runs/*/candidates/001/prompt.txt'):
    text = p.read_text()
    diagnostic = re.search(r'<DIAGNOSTIC[^>]*>(.*?)</DIAGNOSTIC>', text, re.S).group(1).strip()
    evidence.append(dict(path=str(p.relative_to(root)), sha256=hashlib.sha256(p.read_bytes()).hexdigest(), diagnostic=diagnostic))
assert any('undeclared identifier' in e['diagnostic'] for e in evidence)
assert any('output_mismatch' in e['diagnostic'] and 'expected' in e['diagnostic'] and 'actual' in e['diagnostic'] for e in evidence)
record = root / 'fine_launch.json'
assert not record.exists(), 'Do not duplicate full run'
command = [sys.executable, '-B', 'tools/run_bench4hls_compare.py', '--config', 'serve/runtime.formal.json', '--workers', '4', '--cpu-only']
log = root / 'output/fine_formal.log'
with log.open('xb') as out:
    process = subprocess.Popen(command, cwd=root, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
receipt = dict(pid=process.pid, command=command, smoke_evidence=evidence, log=str(log), old_results_reused=False)
record.write_text(json.dumps(receipt, indent=2))
print(json.dumps(receipt, indent=2))
