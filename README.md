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
├── local_eval/               # Mandatory development network-failure restart lifecycle; removable
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
- `agent/core/controller.py` implements bounded generation, public validation, repair and evidence-based candidate selection.
- `evaluation/single_task.py` preserves the legacy manifest CLI and validation exit codes using the shared agent controller.
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

研发期间，上述入口以及 Agent、严格基线、配对、对比评测入口默认必经统一的评测管理模块，无须额外开启。遇到 503 等临时 HTTP 错误或网络异常，整轮作废并自动从头重跑；批量/配对命令由最外层统一重跑，子任务不单独重试。默认最多重跑两次，参数位于 `local_eval/retry.json`。

显式输出目录现在保存整个会话，每轮原始结果位于 `attempt_NNN/result/`；无显式输出目录时会话写入 `output/local_eval/`。先查看 `session.json` 的 `valid_attempt` 再读取成绩，作废轮保留日志但不计分。提交副本移除 `local_eval/` 即恢复原始单轮行为及输出路径。详见 [研发评测与移除说明](local_eval/README.md)。

## Competition baseline entry

`run_baseline.sh` provides a problem-only, one-request entry with a new output
directory and complete request/result artifacts. `run_paired.sh` coordinates it
with `run.sh` under one configuration snapshot and run ID. The agent also has a
Windows `run_agent.ps1` entry and accepts explicit `--task-manifest` public inputs.
See [agent usage](agent/README.md) and [baseline protocol](report/baseline_protocol.md) for usage,
historical-data limitations and the agent receipt contract.

`python -B tools/test_baseline_contract.py` uses a local mock service only.
`python -B tools/test_agent_contract.py` tests the controller and paired entry with
synthetic tasks and mock validators. `tools/smoke_agent_hls.py` runs a synthetic
compile-failure/repair case against real Vitis and a loopback model fixture.

## Git hygiene

The repository tracks framework code, documentation, manifests and reusable scripts. It ignores model weights, raw and processed data, generated build/board artifacts, run artifacts, bytecode and `.env`. Git does not track empty directories; `.gitkeep` preserves the otherwise empty `output/` directory.
