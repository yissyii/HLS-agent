# Bench4HLS baseline vs agent 评测记录（2026-09-17，全量 170 题 · 沙箱复跑）

> 历史实验：本轮使用旧的严格代码块提取规则。当前已采用 `ranked_fences_v1`，更新后另行重跑的报告见 [提取更新后的评测](9-17-Bench4HLS-BLandAgent-ALL-SandBox-ExtractUpdated.md)。本轮结果继续按原口径解释。

## 摘要

在 Bench4HLS 数据集**全量 170 题**上，用**本地 vLLM 端点（127.0.0.1:8001）**重新对比「纯 baseline 一次生成」与「agent 独立生成 + 修复回路」两条入口。agent 的 compile、run、synthesize、overall 均高于 baseline，端到端通过率 **40.0% → 54.7%（+14.7pp，多通过 25 题）**；combinational +20.6pp、sequential +9.8pp，kernel 由 9-16 的「持平」变为 **agent 净领先（7/20 vs 4/20）**。

本次复跑的两类历史问题（模型 API 网络抖动、csim 可执行程序启动异常）在本环境下**均未出现**：本地端点全程无 503/404，`local_eval` 一次通过（`attempt_000`）；全 340 次验证日志中 **0 处** csim 启动异常。9-16 中 5 道「csim 无法启动」题目（034/039/040/043/152）里，**4 道（034/039/040/043）的 agent 现已通过**，仅 Prob152 仍失败——且根因是 FIR 状态未跨调用保存的**功能错误**，与启动无关。

新发现：baseline 的 68 次「生成失败」全部是**响应格式问题**（52 次 `response_format_error` + 16 次 `generation_incomplete`），即模型给出了答案但不符合 harness 的严格提取规则或超出 4096 token 上限；agent 因提示词明确要求「仅返回源码、可选单个 cpp 代码块」，**格式失败为 0**。这使 baseline 的绝对通过率被系统性压低，baseline vs agent 的差距不能全部解读为「修复/编码能力」的因果收益。

> 本文基于本轮沙箱完整重跑的实际结果（`output/local_eval/20260917T094221868852Z-a213469d/`），未复用 9-16 的任何候选或日志。两条入口仍独立采样（temperature=0.7、未指定 seed），因此逐题差异同时混合了提示词差异与采样波动。

## 评测配置

| 项 | 值 |
|---|---|
| 数据集 | Bench4HLS（zfsadik/Bench4HLS @ 7fac5b3），全量 170 题 |
| 模型 | `qwen38`（本地 vLLM，`--max-model-len 16384`，`--max-num-seqs 4`） |
| 端点 | `http://127.0.0.1:8001/v1`（与 9-16 的 ngrok 外部端点不同） |
| 采样配置 | 两条入口均为 `temperature=0.7`，未指定 seed；各自独立生成 |
| HLS 工具 | Vitis HLS 2025.2（WSL2 Linux，`/tools/Xilinx/2025.2/Vitis`），part `xczu3eg-sbva484-1-e` |
| 验证深度 | 生成 + csim 功能验证 + 综合（csynth） |
| 并行度 | 4 workers（与 vLLM `max-num-seqs=4` 对齐） |
| baseline 入口 | `serve/baseline_entry.py`（生成）+ `evaluation.single_task --source`（验证） |
| agent 入口 | `agent.interface.entry --task-manifest`（生成 + 验证 + 最多 2 轮修复） |
| agent 初始提示词 | 题目之外增加 Vitis HLS 源码要求、目标器件、时钟和顶层函数，并明确「仅返回源码、可选单个 cpp 代码块」；不复用 baseline 候选 |
| 修复反馈 | `feedback_policy=category_only`，模型看不到日志具体报错 |
| 停止策略 | `max_repairs=2`、`stagnation_limit=2`；重复候选立即停止 |
| 修复规则包 | `skills_enabled=false`，未启用 |
| 评测规模 | 170 题 × 2 方法 = 340 次评测，单批跑完，耗时约 34 分钟 |

## 题目覆盖（全量 170 题）

- **kernel**（DSP/数值/加密）：20 题（Prob151–170）
- **sequential**（FSM/协议/控制/状态机等）：82 题
- **combinational**（门电路/MUX/加减器/编码译码等）：68 题

> 其中 61 题被结构规则标记为 `too_simple`（53 道 combinational + 8 道 sequential），其余 109 题为非简单题。规则与 9-16 一致：combinational 参考代码非空行数 ≤15；sequential 非空行数 ≤12 且无循环、无数组。

## 总体结果（全量 170 题）

| 指标 | baseline | agent | 增量 |
|---|---:|---:|---:|
| 编译通过 compile | 86 / 170 · **50.6%** | 152 / 170 · **89.4%** | **+38.8pp** |
| csim 功能通过 run | 69 / 170 · **40.6%** | 93 / 170 · **54.7%** | **+14.1pp** |
| 综合通过 synthesize | 68 / 170 · **40.0%** | 93 / 170 · **54.7%** | **+14.7pp** |
| **端到端通过 overall** | 68 / 170 · **40.0%** | 93 / 170 · **54.7%** | **+14.7pp（+25 题）** |
| 平均 API 请求数 | 1.0（102 条有值） | 1.54（170 条有值） | +0.54 |
| 平均耗时 | 13.2s（102 条） | 23.5s（170 条） | 口径不同，不可直接比较 |

统计口径说明：

- 通过率分母均为 170。compile 是评测器依据 csim 日志推断的状态，并非独立编译测量。
- baseline 的 68 条生成失败记录未在汇总顶层填入请求数与耗时，故「平均耗时/请求数」两列的口径不对等（见下文「问题分析」）。
- 本跑总耗时 2059.8s（约 34 分钟），远快于 9-16 的两批（4 workers + 本地模型 + 本地 Vitis）。

## 分大类结果（overall 通过率）

| 大类 | 题数 | baseline | agent | 变化 |
|---|---:|---:|---:|---|
| kernel（DSP/数值/加密） | 20 | 4 · 20.0% | 7 · 35.0% | **+15.0pp** |
| sequential（FSM/协议/控制） | 82 | 27 · 32.9% | 35 · 42.7% | **+9.8pp** |
| combinational（门电路等） | 68 | 37 · 54.4% | 51 · 75.0% | **+20.6pp** |

> 与 9-16 的「kernel 持平（3/20 对 3/20）」不同，本轮 kernel 上 agent 净领先（7 对 4）。绝对数量仍小，需谨慎解读，但至少说明 kernel 的「净增益为零」结论不稳健。

## 按难度分层（overall 通过率）

| 难度 | 题数 | baseline | agent | 变化 |
|---|---:|---:|---:|---|
| 过于简单（too_simple） | 61 | 30 · 49.2% | 48 · 78.7% | +29.5pp |
| 非简单 | 109 | 38 · 34.9% | 45 · 41.3% | +6.4pp |

## 通过题目明细

- baseline 端到端通过：68 题
- agent 端到端通过：93 题
- **仅 agent 通过**（35 题）：Prob004、008、009、015、021、025、035、041、043、048、050、052、054、055、057、059、069、072、087、088、090、096、097、099、103、119、125、126、130、138、154、156、158、161、162
- **仅 baseline 通过**（10 题，agent 在这几题反而失败）：Prob064、091、104、105、118、120、123、133、160、164

## 初始生成与修复收益拆分

两条入口是独立生成，不是同一候选的修复前后对比：baseline 将题目原文传给模型；agent 经 `agent/context/builder.py` 添加 HLS 约束后重新生成，未传入 baseline 的 `candidate.cpp`。即使模型相同，也因提示词不同、温度非零而可能得到不同实现。

| 阶段 | 数量 |
|---|---:|
| baseline 独立生成后 overall 通过 | 68 |
| agent 首轮（candidates[0]）overall 通过 | **82** |
| agent 最终 overall 通过 | **93** |
| agent 从自身失败首稿中挽回 | **11** |

- **修复挽回的 11 题**：Prob017、018、031、051、055、057、063、077、089、114、161。
- agent 首轮通过的 82 题，最终全部仍通过（未发现已通过候选被修坏）。
- 数量上可写成 `93 − 68 = (82 − 68) + (93 − 82) = 14 + 11`，但前面的 +14 混合了提示词差异与采样波动，不能直接解释成提示词的因果收益。
- 对比 9-16（首轮 74、挽回 12、最终 86），本轮 agent 的净提升主要来自首轮通过数上升（74→82），修复挽回数基本持平（12→11）。

## 问题分析（排除网络问题与 csim 启动异常）

本轮两入口均为**零网络失败**（`local_eval` 一次通过，无 `discarded_network`）、**零 csim 启动异常**（340 次验证日志中无 `invalid argument` / `could not execute`）。因此问题集中在以下真实类别。

### 1. 响应格式问题主导了 baseline 的失败（关键差异来源）

baseline 的 68 次「生成失败」（占 40%）全部不是编码能力问题，而是**输出格式**：

| 生成失败类别 | 数量 | 含义 |
|---|---:|---|
| `response_format_error` | 52 | 模型返回了带 Markdown 代码围栏 + 额外说明（或多段代码块）的文本，harness 的 `extract_code` 用 `fullmatch` 严格匹配「整个响应恰好一个 cpp 代码块」，被拒绝 |
| `generation_incomplete` | 16 | 模型达到 `max_tokens=4096` 上限，`finish_reason != stop`，响应被判定为不完整 |

这 68 题中，模型实际上**都给出了答案**，只是不符合提取规则或未写完。baseline 的提示词是题目原文（不含「仅返回源码」约束），导致模型倾向于输出解释性文字。

**agent 的同类失败为 0**：其提示词明确要求「Write a complete Vitis HLS C++ source file. Return source only, optionally in one cpp fence. Do not output a testbench or scripts.」，从根上避免了格式失败。agent 仅有 3 次 `generation_incomplete`（Prob067、081、122，首轮生成超 token，修复未成功）。

**结论**：baseline 的绝对通过率被响应格式问题系统性压低约 68 题。若只看「生成出合法源码的题目」，baseline 在 102 题中通过 68 题（66.7%），并不明显弱于 agent。baseline vs agent 的差距应分解为「格式约束差异」+「修复能力」+「采样波动」三部分，不能简单归为修复或编码能力。

### 2. 编译错误

- baseline：15 题（在 102 题生成成功的题目中，15 题 csim 编译失败）
- agent：14 题

agent 编译错误题目：Prob064、065、078、083、091、101、104、109、110、112、120、135、155、163。

编译错误在两入口数量相当（14–15），说明这部分更多是题目本身的编码难点（如类型转换歧义、不存在的成员调用等，与 9-16 观察一致），而非修复能力差异。`category_only` 反馈下，模型看不到具体报错，修复回路对编译错误的挽回有限。

### 3. 功能错误（agent 的主要失败类别）

| 入口 | 功能错误数 | 占该入口验证题比例 |
|---|---:|---:|
| baseline | 17 | 17/102 ≈ 16.7% |
| agent | 59 | 59/170 ≈ 34.7% |

agent 的功能错误（编译通过、csim 测试不匹配）集中在 **sequential（38 题）与 kernel（10 题）**：

- kernel：10 题功能错误 + 2 编译错误 + 1 超时，仅 7 题通过 → **kernel 是最难大类**，与 9-16 一致。
- sequential：38 题功能错误，是功能错误的主体（时序/状态机题目输出错拍、状态未持久化等）。

agent 的功能错误绝对数高于 baseline，是因为 agent 通过生成阶段进入验证的题目远多于 baseline（170 vs 102），并非 agent 编码更差。功能错误仍是修复回路未能充分挽回的主要缺口：`category_only` 反馈只告知「功能失败」，不提供反例，模型难以定位输出错拍、状态持久化等时序问题。

### 4. 综合错误与超时

- baseline：综合错误 1 题（Prob154）、超时 1 题。
- agent：超时 1 题（Prob153，`tool_timeout`）、综合错误 0 题。

综合错误极少，说明「csim 通过但综合失败」不是本轮的主要瓶颈（9-16 的 Prob162 pragma 综合失败，本轮 agent 已通过）。超时各 1 题，属个别复杂题（kernel 类）超出 300s 综合预算。

## 9-16 启动异常 5 题的复测结果

9-16 中 034/039/040/043/152 因 `csim.exe` 无法启动被误标为编译失败。本轮沙箱复测的真实结果：

| 题目 | baseline | agent | 说明 |
|---|---|---|---|
| Prob034 | ✅ 通过 | ✅ 通过 | 启动异常消失，两边均通过 |
| Prob039 | ✅ 通过 | ✅ 通过 | 同上 |
| Prob040 | ✅ 通过 | ✅ 通过 | 同上 |
| Prob043 | ❌ 响应格式失败 | ✅ 通过 | agent 通过；baseline 是格式问题（非启动） |
| Prob152 | ❌ 编译失败 | ❌ 功能失败 | 两边仍失败，根因是 FIR 状态未跨调用保存（功能），非启动 |

换到 WSL2 Linux Vitis 并重新生成候选后，本轮同一批题目中未观察到启动异常；环境和候选均有变化，尚不能由此确定原 Windows 异常的根因。5 题中 4 题的 agent 现已通过；Prob152 的失败根因与 9-16 的静态复核一致（FIR 状态持久化），属于真实功能缺陷而非环境问题。

## 关键结论

1. **本轮 agent 总体通过数量更高，但不是逐题严格占优**：baseline 68 题、agent 93 题，仅 agent 通过 35 题、仅 baseline 通过 10 题。
2. **baseline 有 40% 的题目未进入验证**：52 次 `response_format_error` 和 16 次 `generation_incomplete`。格式失败与截断应分别报告；不能把截断全部归因于提取规则，差距也不宜直接解读为编码或修复能力。
3. **agent 的格式约束是其重要优势来源**：显式要求「仅返回源码」使 agent 格式失败为 0，170 题全部进入验证。
4. **修复能力受限于 `category_only` 反馈**：本轮修复从自身首稿挽回 11 题（与 9-16 的 12 题相当），功能错误仍是主要缺口，模型看不到反例/具体报错。
5. **kernel 本轮 agent 净领先（7/20 vs 4/20）**，与 9-16「kernel 净增益为零」的本轮观察不同，但绝对数量小，需重复实验确认。
6. **本轮未观察到网络失败或 csim 启动异常**：本地端点 + Linux Vitis 下两者均为 0，5 道历史启动异常题中 4 道的 agent 本轮通过；这不保证后续运行不会再出现基础设施故障。

## 环境与数据产物

- 全量合并结果：`output/local_eval/20260917T094221868852Z-a213469d/attempt_000/artifacts/compare_20260917T094221948750Z/summary.json`（340 条）
- 运行日志：`output/full_run.log`
- 数据集：`data/raw/Bench4HLS/`（@ 7fac5b3）、`data/processed/bench4hls/ProbNNN/`（170 题，测试台全部适配）
- 配置：`serve/runtime.local.json`（本地 vLLM `http://127.0.0.1:8001/v1`）
- 评测脚本：`tools/run_bench4hls_compare.py`、`tools/ingest_bench4hls.py`、`tools/select_bench4hls_tasks.py`

各题 agent 汇总：`<批次>/<题号>/agent/result.json`（含 `candidates` 历史与各轮 `checks`）；具体错误见各候选 `work/csim.log`、`work/synthesis.log`。

## 附录：与 9-16 的关键差异对照

| 维度 | 9-16（Windows + ngrok） | 9-17 沙箱（WSL2 + 本地 vLLM） |
|---|---|---|
| overall | 67 → 86（+19，+11.2pp） | 68 → 93（+25，+14.7pp） |
| kernel | 3 vs 3（持平） | 4 vs 7（agent +3） |
| sequential | 26 vs 35（+11.0pp） | 27 vs 35（+9.8pp） |
| combinational | 38 vs 48（+14.7pp） | 37 vs 51（+20.6pp） |
| agent 首轮通过 / 修复挽回 | 74 / 12 | 82 / 11 |
| 网络失败 | 22 个 503/404 案例 | 0 |
| csim 启动异常 | 5 题（034/039/040/043/152） | 0 |
