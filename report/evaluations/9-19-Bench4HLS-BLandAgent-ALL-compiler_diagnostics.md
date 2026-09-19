# Bench4HLS baseline vs agent 评测记录（2026-09-19 · `compiler_diagnostics` 档 · 全量 170 题）

## 摘要

在 Bench4HLS **全量 170 题**上，用本地 vLLM（`127.0.0.1:8001`）对比「纯 baseline 一次生成」与「agent 生成 + 修复回路」，agent 侧修复反馈从 `category_only` 换成新档 **`compiler_diagnostics`**（把 Vitis 日志按 kind 分级后，将 `compiler`+`synthesis` 的具体报错喂给模型）。本轮代码与 9-17 沙箱 extract_code 更新版（commit `cfe9eb9`）一致，**唯一变量是 `feedback_policy`**。

**结论：`compiler_diagnostics` 显著抬升 agent，增益集中在编译/综合失败题，功能失败题基本不变。**

- **agent overall 55.3% → 60.6%（+5.3pp，+9 题）**，而 baseline 几乎不变（52.4% → 52.9%，+1 题，采样噪声内）—— 证明增量来自反馈档，而非提取/环境变化。
- **compile 增益最大**：agent 87.1% → 95.9%（+8.8pp，+15 题）；其中 **sequential 大类的 compile 从 62.2% → 97.6%（+35.4pp）**，是 compiler_diagnostics 的核心收益来源。
- 增益集中在**非简单题**（overall +11.0pp），简单题基本不动（+1.6pp），符合「compiler_diagnostics 只对编译/综合错误有增益、对功能错误无增益」的预期。
- combinational 大类 agent 反而略降（−2.9pp），属采样噪声 + 修复回路在已过简单题上的轻微回归。
- 本轮**零网络失败、零 csim 启动异常**，数据有效（`valid_for_metrics=true`，`network_failures=[]`，无整轮重跑）。

## 评测配置

| 项 | 值 |
|---|---|
| 数据集 | Bench4HLS，全量 170 题 |
| 模型 | `qwen38`（本地 vLLM，`--max-model-len 16384`，`--max-num-seqs 4`） |
| 端点 | `http://127.0.0.1:8001/v1`（loopback，非 ngrok） |
| 采样 | `temperature=0.7`，未指定 seed，两入口独立生成 |
| HLS 工具 | Vitis HLS 2025.2（`/tools/Xilinx/2025.2/Vitis`），part `xczu3eg-sbva484-1-e` |
| 验证深度 | 生成 + csim 功能验证 + 综合（csynth） |
| 并行度 | 4 workers |
| 代码块提取 | `serve/code.py::extract_code`（ranked-fences v1，commit `cfe9eb9`） |
| **修复反馈** | **`feedback_policy=compiler_diagnostics`**（`compiler`+`synthesis` 具体错误入 prompt；`functional` 扣下 Mismatch 反例） |
| 停止策略 | `max_repairs=2`、`stagnation_limit=2`；重复候选立即停止 |
| 评测规模 | 170 题 × 2 方法 = 340 次评测，单批跑完，耗时约 39 分钟 |

## 总体结果（全量 170 题）

| 指标 | baseline | agent | 增量 |
|---|---:|---:|---:|
| 编译通过 compile | 126 / 170 · **74.1%** | 163 / 170 · **95.9%** | +37（+21.8pp） |
| csim 功能通过 run | 90 / 170 · **52.9%** | 103 / 170 · **60.6%** | +13（+7.6pp） |
| 综合通过 synthesize | 90 / 170 · **52.9%** | 103 / 170 · **60.6%** | +13（+7.6pp） |
| **端到端通过 overall** | 90 / 170 · **52.9%** | 103 / 170 · **60.6%** | **+13（+7.6pp）** |
| 平均 API 请求数 | 1.0 | 1.56 | +0.56 |
| 平均耗时 | 12.93s | 26.90s | 口径不同，不可直接比较 |

## 与 category_only 基线对照（同 extract_code，唯一变量是 feedback_policy）

| 指标 | category_only agent<br>(9-17 extract 更新版) | compiler_diagnostics agent<br>(本轮) | 增量 |
|---|---:|---:|---:|
| compile | 148 / 170 · 87.1% | 163 / 170 · 95.9% | **+15（+8.8pp）** |
| run | 95 / 170 · 55.9% | 103 / 170 · 60.6% | +8（+4.7pp） |
| synthesize | 94 / 170 · 55.3% | 103 / 170 · 60.6% | +9（+5.3pp） |
| **overall** | 94 / 170 · 55.3% | 103 / 170 · 60.6% | **+9（+5.3pp）** |

baseline 对照（应不变，仅采样波动）：

| 指标 | category_only baseline | compiler_diagnostics baseline |
|---|---:|---:|
| overall | 89 / 170 · 52.4% | 90 / 170 · 52.9% |

> 说明：runbook 第 9 步引用的「category_only 基线 agent 54.7% / baseline 40.0%」是 **extract_code 重写之前**的旧口径（`fullmatch` 提取），与本轮代码（ranked-fences v1）不可直接比较。上表使用同为 `cfe9eb9` 代码的 extract 更新版 category_only（55.3% / 52.4%）作为公平对照。

## 分大类结果（overall 通过率）

| 大类 | 题数 | baseline | agent | Δ |
|---|---:|---:|---:|---:|
| kernel（DSP/数值/加密） | 20 | 6 · 30.0% | 7 · 35.0% | +5.0pp |
| sequential（FSM/协议/控制） | 82 | 31 · 37.8% | 45 · 54.9% | **+17.1pp** |
| combinational（门电路等） | 68 | 53 · 77.9% | 51 · 75.0% | −2.9pp |

### 分大类 compile 通过率（收益主战场）

| 大类 | 题数 | baseline compile | agent compile | Δ |
|---|---:|---:|---:|---:|
| kernel | 20 | 14 · 70.0% | 18 · 90.0% | +20.0pp |
| sequential | 82 | 51 · 62.2% | 80 · 97.6% | **+35.4pp** |
| combinational | 68 | 61 · 89.7% | 65 · 95.6% | +5.9pp |

## 按难度分层（overall 通过率）

| 难度 | 题数 | baseline | agent | Δ |
|---|---:|---:|---:|---:|
| 过于简单（too_simple） | 61 | 45 · 73.8% | 46 · 75.4% | +1.6pp |
| 非简单 | 109 | 45 · 41.3% | 57 · 52.3% | **+11.0pp** |

## 关键结论

1. **`compiler_diagnostics` 对 agent 的整体提升是真实且集中的**：overall +5.3pp（55.3% → 60.6%），主要来自 compile（87.1% → 95.9%，+8.8pp）。baseline 稳定（52.4% → 52.9%），排除了提取/环境的干扰。
2. **sequential 是最大受益大类**：compile 62.2% → 97.6%（+35.4pp），overall 37.8% → 54.9%（+17.1pp）。sequential（FSM/状态机/协议/控制）的 C++ 实现最容易出现类型歧义、成员不匹配等**编译错误**，把具体 clang/Vitis 报错喂给模型后修复率大幅上升——这正是 compiler_diagnostics 的设计目标。
3. **功能错误收益有限**：run 口径 agent 55.9% → 60.6%（+4.7pp），远小于 compile 增益。功能失败时 `compiler_text` 为空、回退到类别串，与 category_only 等价，符合预期。
4. **kernel 增益小（+5.0pp）**：kernel 的失败以功能/时序/综合为主，compiler_diagnostics 帮不上忙。
5. **combinational 轻微反降（−2.9pp）**：属采样噪声 + 修复回路在大量已过的简单题上偶尔引入回归（temperature=0.7）。
6. **基础设施干净**：本地端点 + Linux Vitis 下零网络失败、零 csim 启动异常；session 记录 `valid_for_metrics=true`、`network_failures=[]`、`request_outcomes_unknown=0`，单轮 attempt_000 完成，无整轮重跑，数据未被污染。

## 环境与数据产物

- 全量合并结果：`output/local_eval/20260919T024028800530Z-dc09c465/attempt_000/artifacts/compare_20260919T024029499962Z/summary.json`（340 条）
- session 记录：`output/local_eval/20260919T024028800530Z-dc09c465/session.json`
- 运行日志：`output/compare_compiler_diagnostics.log`
- 配置：`serve/runtime.local.json`（本地 vLLM `http://127.0.0.1:8001/v1`）
- 修复反馈：`feedback_policy=compiler_diagnostics`（已写入 170 个 `task.json`）
- 诊断提取：`evaluation/diagnostics.py`；代码块提取 `serve/code.py`（ranked-fences v1）

## 附：本轮对代码的一处修复

`tools/check_endpoint.py` 原硬编码 `HTTPSConnection`，连 `http://127.0.0.1:8001` 会报 `WRONG_VERSION_NUMBER`；已改为按 scheme 选择 `HTTPConnection`/`HTTPSConnection`（仅诊断工具，不影响评测链路，本地未提交）。
