# Remote Claude handoff: RAG-off Workflow Skill ablation

## Frozen target

- Branch carrying this handoff: `feat/workflow-skill-ablation`
- Experiment code commit: `527c9b273f8f0f772c6e7b8f0ad2ad2238efde48`
- Repository on Ubuntu: `/root/AMDCmpt/zcomp`
- Dataset identity: `zfsadik/Bench4HLS@7fac5b356b0383e9463995e2c6b13a5fee27a62d`, exactly `Prob001`–`Prob170`
- Model/runtime: `qwen38` at `http://127.0.0.1:8001/v1`, temperature `0.0`, Vitis/Vivado `2026.1`

The handoff document is committed after the experiment code, so read it from the remote branch first and then detach at the exact experiment commit. Do not silently substitute the older `feat/rag` branch.

## Objective

Measure the incremental effect of project-native Workflow Skills with RAG and legacy repair rules disabled. This is a **frozen benchmark replay**, not a held-out generalization claim.

| Condition | Problem Contract | Generated Self-Test | Synth Guard |
| --- | ---: | ---: | ---: |
| S0 | off | off | off |
| S1 | on | off | off |
| S2 | on | on | off |
| S3 | on | on | `observe` |

All four conditions are independent end-to-end runs. Never reuse an S0 draft for S1: Contract changes the first-generation prompt. S1–S3 should produce identical Contract and candidate 0 under deterministic inference; the runner treats a mismatch as a protocol failure. Synth Guard is advisory and must not replace or suppress real Vitis synthesis.

## Checkout and environment preflight

```bash
set -euo pipefail
cd /root/AMDCmpt/zcomp
git fetch origin feat/workflow-skill-ablation
git show origin/feat/workflow-skill-ablation:docs/handoff/remote_claude_skill_ablation.md >/tmp/remote_claude_skill_ablation.md
test -z "$(git status --porcelain --untracked-files=no)"
git switch --detach 527c9b273f8f0f772c6e7b8f0ad2ad2238efde48
test "$(git rev-parse HEAD)" = "527c9b273f8f0f772c6e7b8f0ad2ad2238efde48"

source /tools/Xilinx/2026.1/Vitis/settings64.sh
source /tools/Xilinx/2026.1/Vivado/settings64.sh
export XILINXD_LICENSE_FILE=/root/.Xilinx/Xilinx.lic
export PYTHONUTF8=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONHASHSEED=0
export TOKENIZERS_PARALLELISM=false

command -v python3
command -v vitis_hls
test -f "$XILINXD_LICENSE_FILE"
curl -fsS http://127.0.0.1:8001/v1/models | python3 -m json.tool
```

Use system `python3`; do not activate `/root/hls-rag/venv`. The RAG runtime and embedding/reranker models are deliberately outside this experiment.

## Code preflight

```bash
python3 -B tools/test_synth_guard.py
python3 -B tools/test_diagnostics.py
python3 -B -m unittest tools.test_functional_diagnostics -v
python3 -B tools/test_skill_compare.py
python3 -B tools/test_agent_contract.py
git diff --check 527c9b273f8f0f772c6e7b8f0ad2ad2238efde48
test -z "$(git status --porcelain --untracked-files=no)"
```

Expected local contract counts at the target commit are: Synth Guard 8, diagnostics 9, functional-diagnostics module 53 (it includes the Agent contract base), Skill comparison 4, and standalone Agent contract 42.

## Execution sequence

Run only one worker. Preserve the runner's printed summary path; local-evaluation wrapping places the accepted batch below an `attempt_*/result` directory.

### 1. Six-task infrastructure smoke

```bash
python3 -X utf8 -B tools/run_skill_compare.py \
  --selection experiments/skill-ablation/smoke.json \
  --conditions S0,S1,S2,S3 \
  --workers 1 \
  --dataset data/processed/bench4hls \
  --config serve/runtime.rag-eval.json \
  --output output/remote-skill-ablation/smoke-527c9b2
```

Run `python3 -B tools/audit_skill_compare.py <accepted-batch-directory>` on the exact directory containing `summary.json`. Continue only if both the runner and auditor report a valid protocol. Candidate failures are experimental outcomes; protocol, network, license, tool, hash, or RAG-contamination failures are not.

### 2. Frozen 20-task development replay

```bash
python3 -X utf8 -B tools/run_skill_compare.py \
  --selection experiments/skill-ablation/development-20.json \
  --conditions S0,S1,S2,S3 \
  --workers 1 \
  --dataset data/processed/bench4hls \
  --config serve/runtime.rag-eval.json \
  --output output/remote-skill-ablation/development20-527c9b2
```

Audit the accepted batch exactly as above. Do not tune prompts, policies, Skills, task manifests, or stopping rules after looking at results. If a change is genuinely required, stop and request a new commit and a full restart from smoke.

### 3. Full 170-task replay

```bash
python3 -X utf8 -B tools/run_skill_compare.py \
  --all \
  --conditions S0,S1,S2,S3 \
  --workers 1 \
  --dataset data/processed/bench4hls \
  --config serve/runtime.rag-eval.json \
  --output output/remote-skill-ablation/full-527c9b2
```

Audit the accepted batch on completion. Do not infer task selection from any residual `selection.json`; the runner requires `--all`, `--selection`, or `--task` explicitly.

## Mandatory stop conditions

Stop immediately and retain all evidence if any of these occurs:

- Git commit, config, policy, Skill, selected task input, or dataset index changes.
- Vitis/Vivado 2026.1, the license, or `qwen38` is unavailable.
- Temperature differs from `0.0`, worker count differs from one, or repair budget differs from two.
- A receipt has `rag_enabled != false`, nonempty `rag_history`, a retrieval artifact, or `<REFERENCE_MATERIAL>` in a prompt.
- Contract or generated-test evidence is created after candidate 0.
- A hash binding fails, required receipt/event/check evidence is missing, or S1–S3 Contract/candidate 0 hashes differ.
- The local-evaluation wrapper has no accepted attempt.

Do **not** selectively rerun model-generation failures, malformed/ambiguous Contracts, invalid generated tests, context overflow, or Skill-caused regressions. Those are measured outcomes. Whole-round retry is allowed only when the repository's local-evaluation layer invalidates the round for a recorded network failure.

## Evidence and report

Keep every `summary.json`, per-run `result.json`, `events.jsonl`, frozen config/policy/Skill snapshot, candidate, Contract, test plan, generated testbench, self-test result, and Synth Guard result. Do not edit generated receipts.

For each condition report:

- candidate-0 and final compile/run/synthesis/overall counts;
- S1−S0, S2−S1, S3−S2, and S3−S0 per-task wins/losses/ties;
- failed-first-candidate recovery count;
- model/generation/workflow requests, token usage, tool calls, and mean/median elapsed time;
- Contract ready/failure counts, Self-Test runs/failures, and Synth Guard runs/findings;
- every protocol or infrastructure failure separately from candidate quality.

Return the accepted batch paths, auditor outputs, exact commit, start/finish times, and a concise interpretation. Do not claim causal benefit from S3 pass-rate changes: Synth Guard is observe-only in this protocol; S3 primarily measures detection coverage, false-positive review burden, and overhead.
