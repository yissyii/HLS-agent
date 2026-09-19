# Bench4HLS RAG 首次测评记录（2026-09-19 · 关闭 / BM25 / hybrid · 全量 170 题）

> 后续复盘与修正：[RAG 逻辑改进记录](../report/design/rag_repair_v2.md)。本文保留首轮原始统计和当时的判断；因果结论及实验可比性限制见复盘。


## 摘要

在 Bench4HLS **全量 170 题**上，用本地 vLLM（`127.0.0.1:8001`）对比修复阶段的三种检索条件：关闭 / BM25 / hybrid。首稿通过 `temperature=0` + 两阶段复用**固定为同一份**，唯一变量是修复阶段的检索模式，因此是受控实验。

**结论：UG1399 手册检索对修复无增益，且随检索强度递增而略降。**

- **overall：off 112（65.9%）> bm25 111（65.3%）> hybrid 109（64.1%）**，hybrid 相对关闭 −3 题（−1.8pp）。
- compile 三条件完全一致（166/170 = 97.6%），说明差异全部落在修复阶段的 run/synthesis 上。
- 64 题首稿失败触发修复：**off 修复 8 题（12.5%）> bm25 7 题（10.9%）> hybrid 5 题（7.8%）**——检索没有救回更多题，反而随检索增强而减少。
- 分大类：kernel 上 RAG 略有利（+1 题），sequential 上 RAG 明显不利（−2/−4 题），combinational 无影响（58/58/58）。

## 评测配置

| 项 | 值 |
|---|---|
| 数据集 | Bench4HLS，全量 170 题（kernel 20 / sequential 82 / combinational 68） |
| 模型 | `qwen38`（本地 vLLM，`--max-model-len 16384`，`--max-num-seqs 4`） |
| 端点 | `http://127.0.0.1:8001/v1`（loopback） |
| 采样 | **`temperature=0.0`**（贪心解码） |
| **首稿固定方式** | 两阶段：off 串行生成 170 个首稿 → bm25/hybrid 经 `--initial-source` 复用同一首稿 |
| HLS 工具 | Vitis HLS 2025.2（`/tools/Xilinx/2025.2/Vitis`），part `xczu3eg-sbva484-1-e` |
| 验证深度 | 生成 + csim 功能验证 + 综合（csynth） |
| 并行度 | Phase 1 off 串行（workers=1）；Phase 2 bm25/hybrid 并行（workers=4） |
| **修复反馈** | **`feedback_policy=compiler_diagnostics`**（`compiler`+`synthesis` 具体错误入 prompt） |
| 停止策略 | `max_repairs=2`、`stagnation_limit=2`；重复候选立即停止 |
| **RAG 预算** | `rag_recall_k=20`、`rag_top_k=3`、`rag_max_bytes=6000`、`rag_query_max_chars=1600`、`rag_timeout_seconds=30` |
| 检索模型 | `Qwen3-Embedding-0.6B`（本地 CPU，revision `97b0c614…`） |
| 语料 | UG1399 v2025.2 英文手册（904 页，1756 条目），release `ug1399-2025.2-en-reference-1` |

## 总体结果（全量 170 题）

| 指标 | off（关闭） | bm25 | hybrid | 关闭→hybrid 增量 |
|---|---:|---:|---:|---:|
| 编译通过 compile | 166 / 170 · **97.6%** | 166 / 170 · **97.6%** | 166 / 170 · **97.6%** | 0 |
| csim 功能通过 run | 113 / 170 · 66.5% | 112 / 170 · 65.9% | 110 / 170 · 64.7% | −3 |
| 综合通过 synthesize | 112 / 170 · 65.9% | 111 / 170 · 65.3% | 109 / 170 · 64.1% | −3 |
| **端到端通过 overall** | **112 / 170 · 65.9%** | **111 / 170 · 65.3%** | **109 / 170 · 64.1%** | **−3（−1.8pp）** |
| 平均 API 请求数 | 1.42 | 0.42 | 0.41 | — |
| 平均耗时 | 25.43s | 21.00s | 23.31s | — |

> 说明：bm25/hybrid 的 `mean_api_req≈0.42` 不含首稿生成（被 `--initial-source` 跳过），off 的 1.42 含 1 次生成；三者修复阶段的请求数相当（≈0.42）。

## 修复恢复率（首稿失败题上的增量）

固定首稿后，**104 题首稿直接通过**、**64 题首稿失败触发修复**（另有 2 题生成级失败无有效首稿：Prob067、Prob141）。三条件在这 64 题上的修复恢复：

| 条件 | 修复成功 | 修复恢复率 |
|---|---:|---:|
| off（关闭） | 8 / 64 | **12.5%** |
| bm25 | 7 / 64 | 10.9% |
| hybrid | 5 / 64 | **7.8%** |

## 分大类结果（overall 通过率）

| 大类 | 题数 | off | bm25 | hybrid | 关闭→hybrid |
|---|---:|---:|---:|---:|---:|
| kernel（DSP/数值/加密） | 20 | 9 · 45.0% | 10 · 50.0% | 10 · 50.0% | **+1** |
| sequential（FSM/协议/控制） | 82 | 45 · 54.9% | 43 · 52.4% | 41 · 50.0% | **−4** |
| combinational（门电路等） | 68 | 58 · 85.3% | 58 · 85.3% | 58 · 85.3% | 0 |

## 检索注入证据

| 条件 | rag_enabled | 至少注入一次的题 | 累计注入条目 |
|---|---:|---:|---:|
| bm25 | 170 | 63 | 181 |
| hybrid | 170 | 62 | 185 |

检索链路实际生效（62–63 题在修复阶段注入了手册条目，累计 181–185 条），注入的证据见各轮 `candidates/{n:03d}/retrieval.json` 与 `result.json` 的 `rag_history`。

## stop_reason 分布（全部 510 次）

| stop_reason | 次数 | 说明 |
|---|---:|---|
| validation_passed | 332 | 验证通过 |
| repeated_candidate | 97 | 修复产出与既有候选重复 → 停止 |
| stagnation | 64 | 修复停滞 |
| repair_budget_exhausted | 6 | 修复次数用尽 |
| context_budget_exceeded | 3 | 上下文超限 |
| tool_timeout | 3 | Vitis 工具超时 |
| generation_incomplete | 2 | 首稿截断（Prob067、Prob141） |
| response_format_error | 2 | 响应格式错误 |
| **rag_timeout** | **1** | **Prob030 hybrid 检索超时** |

## 关键结论

1. **UG1399 检索对修复无增益，且随检索强度（off → bm25 → hybrid）递增而略降**：overall 65.9% → 65.3% → 64.1%；64 个首稿失败题上修复恢复率 12.5% → 10.9% → 7.8%。差异约 3 题，落在 Phase 2 并行的修复采样噪声边缘，但方向一致（非正向）。
2. **compile 三条件完全一致（97.6%）**：固定首稿 + 同一 feedback 后，编译层面已无 RAG 可发挥的空间，差异全部来自功能（run）与综合（synthesis）。
3. **kernel 略有利、sequential 明显不利、combinational 无影响**：kernel 失败以功能/时序为主，手册语义提示略有用（+1 题）；sequential（FSM/协议）的 C++ 实现错误多为类型/成员不匹配，通用手册段落不针对具体任务、反而占用了上下文预算（−2/−4 题）。
4. **检索是「参考材料」而非「验证过的修复」**：UG1399 条目 `validation=unvalidated`、`release=reference`，不包含 Bench4HLS 任务级的修复方案；注入的 6000 字节参考区可能挤占 prompt 里真正有用的 diagnostic/代码空间。
5. **基础设施干净**：全量 510 次仅 1 次 `rag_timeout`（Prob030 hybrid，Phase 2 并行下模型冷启动 CPU 争用）、零网络失败、零 csim 启动异常；`generation_incomplete`/`response_format_error`/`context_budget_exceeded`/`tool_timeout` 均为确定性合法失败，无整轮重跑，数据未被污染。

## 检索失效机理（数据核查）

对注入证据的抽查揭示了「检索无增益且略降」的具体机制。

### 1. 78% 的修复反馈是「空壳」

212 次修复轮次的诊断类别分布：

| 诊断类别 | 次数 | 占比 | 反馈内容 |
|---|---:|---:|---|
| functional_or_runtime_error | 166 | 78.3% | 仅一个类别字符串，无具体报错/反例 |
| compile_error | 43 | 20.3% | 有具体 clang 报错文本 |
| synthesis_error | 3 | 1.4% | 有具体综合报错 |

`feedback_policy=compiler_diagnostics` 只释放 compiler/synthesis 的具体报错，功能失败（csim 比对不过）只回传 `functional_or_runtime_error` 一个类别。因此 78% 的检索 query 是「题目描述 + 一句无信息量的类别串」，检索器只能靠题目去匹配手册，结果跑偏。

### 2. 检索命中质量抽查

| 题 | 真实编译报错 | RAG 检索到的章节 | 相关性 |
|---|---|---|---|
| Prob072 | `invalid digit 'b'`（写成 Verilog `2'b01`） | Initialization from Constants (Literals) | ✅ 命中 |
| Prob050 | `no member 'parity' in ap_uint<100>` | Class Methods / Deprecated / Port Protocols | 半对 |
| Prob088 | `y(0)`（Verilog 索引，应为 `y[0]`） | Fixed-Point Math / Print Function / Constants | ❌ 跑偏 |
| Prob109 | `x(0)` 索引错误 | Dealing with Unsupported Functions（ap_float） | ❌ 跑偏 |

compile 类报错（20%）检索偶有命中（如 `2'b01`→「常量初始化」），但功能类（78%）无从匹配。

### 3. 噪声挤占信号（Prob033 八 D 触发器修复 prompt）

| prompt 构成 | 大小 | 占比 |
|---|---:|---:|
| `<REFERENCE_MATERIAL>`（检索材料） | 5334 字符 | 84% |
| `<CURRENT_SOURCE>`（待修代码） | 115 字符 | 2% |
| `<DIAGNOSTIC>` | "functional_or_runtime_error" | — |

检索材料（Port-Level Protocols、RTL Blackbox JSON 等无关段落）占据 84% 上下文，真正要改的代码只剩 2% 篇幅，模型注意力被无关文本稀释——这解释了为何注入越多（hybrid）反而降得越多。

## 局限与后续

- 本结论仅针对「UG1399 通用手册」这一语料与「compiler_diagnostics 反馈档」的组合；不排除**任务级案例库**（Vitis HLS Introductory Examples / 官方库组件 / 历史实验案例）在修复阶段更有针对性。
- 修复请求在 Phase 2 用 workers=4 并发，存在采样级非确定性；overall 的 3 题差异与 noise 同量级，方向可参考、幅度不宜过度解读。
- 后续可尝试：语料换成「已验证代码示例」、增大 `rag_top_k`/`max_bytes`、或仅对 kernel 大类启用检索。
- 更根本的方向：**给功能失败也释放 csim 的具体反例**（如 `public_diagnostics` 档），让 78% 的功能类修复有可匹配的检索信号，而不是只有一句 `functional_or_runtime_error`。

## 环境与数据产物

- 汇总结果：`output/local_eval/20260919T083533283208Z-b03e6de6/attempt_000/artifacts/rag_compare_20260919T083533352503Z/summary.json`
- session 记录：`output/local_eval/20260919T083533283208Z-b03e6de6/session.json`
- 评测脚本：`tools/run_rag_compare.py`（两阶段固定首稿）
- 配置：`serve/runtime.rag-eval.json`（temperature=0）、`agent/config/policy.rag-bm25.json`、`agent/config/policy.rag-hybrid.json`、`rag/runtime.local.json`
