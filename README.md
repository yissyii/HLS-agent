# Windows HLS Harness

用于 HLS C/C++ 代码生成、Vitis 验证和有限次数修复的 Windows 框架。仓库不包含赛题、参考实现、测试台、模型权重、数据集或历史运行产物。

## Directory layout

```text
zcomp-windows-harness/
├── README.md                 # Project overview and reproduction guide
├── Dockerfile                # Container build contract placeholder
├── model/                    # Model declaration; no weights are committed
├── agent/                    # Agent control-flow source and documentation
├── skill/                    # Reusable HLS skills and validation notes
├── serve/                    # OpenAI-compatible inference client and runtime config
├── evaluation/               # Manifest loading, C simulation, HLS synthesis and repair
├── tools/                    # Standalone operational checks
├── src/                      # Source-code map for the current Python layout
├── sim/                      # Simulation and verification conventions
├── build/                    # Reproducible build scripts; generated artifacts ignored
├── board/                    # Board, toolchain and implementation-output conventions
├── data/                     # Dataset manifests and split metadata; datasets ignored
├── report/                   # Design report and model-collaboration records
├── output/                   # Per-run artifacts; ignored except .gitkeep
└── run_*.ps1                 # Windows PowerShell entry points
```

All repository paths use lowercase English names. The README files in placeholder directories state what may be committed there and what must remain ignored.

## Current implementation

- `serve/inference.py` sends one OpenAI-compatible generation request and records response metadata.
- `evaluation/single_task.py` runs a task manifest through C simulation and Vitis HLS synthesis; it can make bounded repair requests after code failures.
- `evaluation/batch.py` runs manifests sequentially.
- `evaluation/hls.py` starts `E:/2025.2/Vitis/bin/vitis-run.bat`, isolates child environment variables and terminates timed-out Windows process trees.
- `tools/check_endpoint.py` is a minimal endpoint diagnostic. It may contact the configured model endpoint.

The checked-in `serve/runtime.json` contains machine-specific development defaults: `E:/2025.2`, part `xczu3eg-sbva484-1-e`, 5 ns clock, and the current development endpoint. Before sharing outside the team, replace local paths and endpoint settings with a portable configuration or document the required overrides.

## Task contract

Tasks are intentionally external to this repository. A task directory supplied to `evaluation/single_task.py` must contain `task.json`, its problem text, a self-checking C/C++ testbench and every declared dependency. The manifest format is implemented in `evaluation/single_task.py`; model-facing prompts must include only permitted task material. Do not add official hidden-test answers, reference implementations or answer-derived ASTs to `skill/`, `data/`, prompts or retrieval material.

## Running locally

Use Python 3.10+ and Vitis 2025.2. The PowerShell scripts set UTF-8 output and disable bytecode creation for their child process.

```powershell
cd C:\Users\yissyii\zcomp-windows-harness

# Validate a supplied task and an existing candidate; no model call.
.\run_eval.ps1 <task-directory>\task.json --source <candidate.cpp> --cpu-only

# One direct generation, without agent retries.
.\run_baseline.ps1 <problem.txt> output\response.cpp

# Generate, validate and optionally repair within the configured budget.
.\run_eval.ps1 <task-directory>\task.json --cpu-only --repair-attempts 2

# Sequentially evaluate all task manifests below an external task root.
.\run_batch.ps1 <task-root> --cpu-only
```

`output/` is recreated automatically. Do not commit response text, generated code, logs, synthesized RTL, reports, credentials or tool caches.

## Competition baseline entry

`run_baseline.sh` provides a problem-only, one-request entry with a new output
directory and complete request/result artifacts. `run_paired.sh` coordinates it
with a supplied `run.sh` under one configuration snapshot and run ID. The actual
agent `run.sh` is not implemented locally yet; pairing fails before model access
when it is missing. See [baseline protocol](report/baseline_protocol.md) for usage,
historical-data limitations and the agent receipt contract.

`python -B tools/test_baseline_contract.py` uses a local mock service only.

## Git hygiene

The repository tracks framework code, documentation, manifests and reusable scripts. It ignores model weights, raw and processed data, generated build/board artifacts, run artifacts, bytecode and `.env`. Git does not track empty directories; `.gitkeep` preserves the otherwise empty `output/` directory.
