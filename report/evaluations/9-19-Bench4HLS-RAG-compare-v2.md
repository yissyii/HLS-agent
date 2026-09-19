# Bench4HLS RAG 第二轮测评（2026-09-19 · functional_diagnostics + diagnostic_v1 · 170 题）

## 摘要

在 Bench4HLS 全量 170 题上，对比修复阶段 off / bm25 / hybrid 三条件，反馈档改为 **`functional_diagnostics`**（结构化功能反例），检索策略改为 **`diagnostic_v1`**（诊断优先查询 + 词项证据门 + 小预算 + 低信息跳过检索）。首稿复用第一轮的 169 个有效首稿（`--drafts-from`），并新增**共享首稿验证**（Vitis 验证一次、三条件复用），保持确定性。

**结论：functional_diagnostics 让修复收益 +3（off 112→115），而 RAG（diagnostic_v1）仍是中性——无进一步增益，但第一轮的负效应已消除。**

- **overall：off 115（67.6%）= hybrid 115（67.6%）> bm25 114（67.1%）**。逐题配对：hybrid 相对 off **0 胜 0 负 170 平**（完全持平）；bm25 相对 off **0 胜 1 负 169 平**（仅 Prob075 一题略降）。
- 对比第一轮：off 从 112 → 115（+3，来自 functional_diagnostics 结构化反馈）；hybrid 从 109 → 115（+6），**第一轮 hybrid 相对 off 的 −3 负效应消失**。
- **检索几乎不再触发**：仅 9 题实际注入（第一轮 62–63 题）。因为 diagnostic_v1 的低信息门把 61 个功能失败题判为「只有反例数值、无手册信号」，跳过检索、继续无参考修复——这正是第一轮「无关材料占 84% 上下文」的根因被切断。

## 评测配置

| 项 | 值 |
|---|---|
| 数据集 | Bench4HLS，全量 170 题（kernel 20 / sequential 82 / combinational 68） |
| 模型 / 端点 | `qwen38`，`http://127.0.0.1:8001/v1`（本地 vLLM，`--max-num-seqs 4`） |
| 采样 | `temperature=0.0`（贪心） |
| **反馈档** | **`functional_diagnostics`**（compile/synthesis 具体报错 + 功能失败结构化反例） |
| **检索策略** | **`diagnostic_v1`**（诊断优先查询、词项门、`rag_top_k=2`、`rag_max_bytes=2400`） |
| **首稿** | 复用第一轮 169 个有效首稿（`--drafts-from`），Prob067 首稿无效自动排除 |
| **共享验证** | 首稿 Vitis 验证 1 次，三条件复用 `ValidationResult`（跳过重复 csim+synth） |
| 并行度 | workers=1（确定性协议） |
| 停止策略 | `max_repairs=2`、`stagnation_limit=2` |
| HLS 工具 | Vitis HLS 2025.2，part `xczu3eg-sbva484-1-e` |

## 总体结果（全量 170 题）

| 指标 | off | bm25 | hybrid |
|---|---:|---:|---:|
| compile | 166 · 97.6% | 166 · 97.6% | 166 · 97.6% |
| csim/run | 116 · 68.2% | 115 · 67.6% | 116 · 68.2% |
| synthesis | 115 · 67.6% | 114 · 67.1% | 115 · 67.6% |
| **overall** | **115 · 67.6%** | **114 · 67.1%** | **115 · 67.6%** |
| mean_api_req | 0.476 | 0.471 | 0.471 |

## 逐题配对（vs off，固定首稿下的精确对比）

| 对比 | wins | losses | ties |
|---|---:|---:|---:|
| bm25 vs off | 0 | 1（Prob075） | 169 |
| hybrid vs off | 0 | 0 | **170** |

## 分大类 overall

| 大类 | 题数 | off | bm25 | hybrid |
|---|---:|---:|---:|---:|
| kernel | 20 | 9 · 45.0% | 9 · 45.0% | 9 · 45.0% |
| sequential | 82 | 48 · 58.5% | 47 · 57.3% | 48 · 58.5% |
| combinational | 68 | 58 · 85.3% | 58 · 85.3% | 58 · 85.3% |

## 检索注入证据（关键：检索几乎不再触发）

| 条件 | 至少注入一次的题 | 低信息跳过检索 | 累计注入条目 |
|---|---:|---:|---:|
| bm25 | 9 | 61 | 13 |
| hybrid | 9 | 61 | 12 |

diagnostic_v1 的低信息门把「功能失败但只有 expected/actual 数值」的 61 题判为无手册信号，**跳过检索、继续无参考修复**；只有明确的运行异常/死锁或编译错误才会真正检索。检索注入量从第一轮的 62–63 题骤降到 9 题，无关注入噪声被切断。

## stop_reason 分布（510 次）

| stop_reason | 次数 |
|---|---:|
| validation_passed | 344 |
| repeated_candidate | 109 |
| stagnation | 39 |
| repair_budget_exhausted | 9 |
| no_valid_initial_source | 3 |
| context_budget_exceeded | 3 |
| tool_timeout | 3 |

零网络失败、零 RAG 错误、零 csim 启动异常，数据干净。

## 关键结论

1. **functional_diagnostics 有真实收益（+3）**：off 从第一轮 112 → 本轮 115。功能失败时模型拿到结构化反例（`output_mismatch` 的 expected/actual/cycle），而不是旧档的空壳 `functional_or_runtime_error`，修复恢复率上升。
2. **RAG（diagnostic_v1）是中性**：hybrid 与 off **完全持平（170 平）**，bm25 仅 1 题略降。诊断优先查询 + 词项门 + 小预算（2400 字节/2 条）把无关注入压到接近零，但也因此几乎没有「检索真正帮上忙」的场景。
3. **第一轮的负效应被消除**：第一轮 hybrid 相对 off −3（64.1% vs 65.9%），本轮 hybrid = off（67.6%）。根因是低信息门不再把「功能失败 + 一句空壳类别」拿去匹配手册、注入 84% 的无关材料。
4. **UG1399 通用手册对 Bench4HLS 修复仍无正增益**：即使在有具体诊断的编译错误上检索偶有命中（如 `2'b01`→「常量初始化」），逐题配对也没有任何一题因检索而多修好——手册段落是通用参考，不针对任务级错误。

## 与第一轮对照

| 条件 | 第一轮（compiler_diagnostics + legacy RAG） | 第二轮（functional_diagnostics + diagnostic_v1） | Δ |
|---|---:|---:|---:|
| off | 112 · 65.9% | 115 · 67.6% | **+3** |
| bm25 | 111 · 65.3% | 114 · 67.1% | +3 |
| hybrid | 109 · 64.1% | 115 · 67.6% | **+6** |
| hybrid − off | −3 | 0 | — |

## 环境与数据产物

- 汇总：`output/local_eval/20260919T120517084964Z-c08ebbc9/attempt_000/artifacts/rag_compare_20260919T120518996846Z/summary.json`
- 脚本：`tools/run_rag_compare.py`（`--drafts-from` + 共享验证）；`agent/core/controller.py`、`agent/interface/entry.py`（`--initial-validation`）
- 反馈档：`data/processed/bench4hls/*/task.json` 已改 `functional_diagnostics`（本地 gitignore）
