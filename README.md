# HLS Agent Harness

用于 HLS C/C++ 代码生成、Vitis 验证和有限次数修复的框架，提供 Windows PowerShell 和 Linux Shell 入口。源码仓库不提交题集、模型权重或运行产物；本地数据与输出目录由 Git 忽略。

文档导航：[报告与证据索引](report/README.md) · [项目结构思维导图](report/design/project_structure.md) · [Agent 与裸基线入口流程图](report/design/agent_flow.md)。

## Directory layout

```text
zcomp/
├── README.md                 # Project overview and reproduction guide
├── Dockerfile                # Container build contract placeholder
├── model/                    # Model declaration; no weights are committed
├── agent/                    # Agent control-flow source and documentation
├── skill/                    # Reusable HLS skills and validation notes
├── rag/                      # Offline UG1399 retrieval, local embeddings and retrieval evaluation
├── serve/                    # OpenAI-compatible inference client and runtime config
├── evaluation/               # Manifest loading, C simulation, HLS synthesis and repair
├── local_eval/               # Mandatory development network-failure restart lifecycle; removable
├── tools/                    # Standalone operational checks
├── src/                      # Source-code map for the current Python layout
├── sim/                      # Simulation and verification conventions
├── build/                    # Build conventions; generated artifacts ignored
├── board/                    # Board, toolchain and implementation-output conventions
├── data/                     # Dataset manifests and split metadata; datasets ignored
├── report/                   # Design report and model-collaboration records
├── output/                   # Per-run artifacts; ignored except .gitkeep
├── run.sh / run_baseline.sh / run_paired.sh  # Agent, baseline and paired entries
└── run_*.ps1                 # Windows PowerShell entry points
```

Code directories use lowercase English names; archived reports retain their historical filenames. Directory README files explain their responsibilities and what may be committed.

## Current implementation

- `serve/inference.py` sends one OpenAI-compatible generation request and records response metadata.
- `agent/core/controller.py` implements bounded generation, public validation, repair and evidence-based candidate selection.
- `evaluation/single_task.py` preserves the legacy manifest CLI and validation exit codes using the shared agent controller.
- `evaluation/batch.py` runs manifests sequentially.
- `evaluation/hls.py` reads the configured Vitis path, selects `bin/vitis-run.bat` on Windows or `bin/vitis-run` on Linux, isolates child environments and terminates timed-out process trees.
- `tools/check_endpoint.py` is a minimal endpoint diagnostic. It may contact the configured model endpoint.

英文 UG1399 知识库与本地 Qwen3-Embedding-0.6B 的安装、建库、BM25/混合检索和离线检索评估见 [RAG 使用说明](rag/README.md)，进度见 [任务清单](rag/TASKS.md)。该模块目前独立运行，Agent 默认流程尚未接入检索。

The checked-in `serve/runtime.json` contains machine-specific development defaults: `E:/2026.1`, part `xczu3eg-sbva484-1-e`, 5 ns clock, and the current development endpoint. Before sharing outside the team, replace local paths and endpoint settings with a portable configuration or document the required overrides.

## Task contract

Tasks are supplied explicitly and are not committed with the framework. A task directory supplied to `evaluation/single_task.py` must contain `task.json`, its problem text, a self-checking C/C++ testbench and every declared dependency. The shared manifest loader is `evaluation/task_io.py`. The Agent entry also supports problem-only generation or a manifest without a testbench for synthesis-only validation. Model-facing prompts include only permitted material; do not add hidden-test answers, reference implementations or answer-derived ASTs to skills or retrieval material.

## Running locally

Use Python 3.10+ and Vitis 2026.1. The PowerShell scripts set UTF-8 output and disable bytecode creation for their child process.

```powershell
cd F:\Projects\ADMCmpt\zcomp

# Validate a supplied task and an existing candidate; no model call.
.\run_eval.ps1 <task-directory>\task.json --source <candidate.cpp> --cpu-only

# One direct generation, without agent retries.
.\run_baseline.ps1 <problem.txt> output\response.cpp

# Generate, validate and optionally repair within the configured budget.
.\run_eval.ps1 <task-directory>\task.json --cpu-only --repair-attempts 2

# Sequentially evaluate all task manifests below an external task root.
.\run_batch.ps1 <task-root> --cpu-only
```

`output/` is recreated automatically. Do not commit raw responses, generated code, logs, synthesized RTL, tool reports, credentials or caches. Curated Markdown reports belong in `report/`.

研发评测永久采用请求级基础设施重试：保留已完成结果，只重试发生外部服务或网络故障的请求，不再整轮作废重跑。默认最多额外重试两次；未恢复项单列为未完成，不计入能力失败。详见 `local_eval/README.md`。

显式输出目录现在保存整个会话，每轮原始结果位于 `attempt_NNN/result/`；无显式输出目录时会话写入 `output/local_eval/`。先查看 `session.json` 的 `valid_attempt` 再读取成绩，作废轮保留日志但不计分。提交副本移除 `local_eval/` 即恢复原始单轮行为及输出路径。详见 [研发评测与移除说明](local_eval/README.md)。

## Competition baseline entry

`run_baseline.sh` provides a problem-only, one-request entry with a new output
directory and complete request/result artifacts. `run_paired.sh` coordinates it
with `run.sh` under one configuration snapshot and run ID. The agent also has a
Windows `run_agent.ps1` entry and accepts explicit `--task-manifest` public inputs.
See [agent usage](agent/README.md) and [baseline protocol](report/reproducibility/baseline_protocol.md) for usage,
historical-data limitations and the agent receipt contract.

`python -B tools/test_baseline_contract.py` uses a local mock service only.
`python -B tools/test_agent_contract.py` tests the controller and paired entry with
synthetic tasks and mock validators. `tools/smoke_agent_hls.py` runs a synthetic
compile-failure/repair case against real Vitis and a loopback model fixture.

## Git hygiene

The repository tracks framework code, documentation, manifests and reusable scripts. It ignores model weights, raw and processed data, generated build/board artifacts, run artifacts, bytecode and `.env`. Git does not track empty directories; `.gitkeep` preserves the otherwise empty `output/` directory.
