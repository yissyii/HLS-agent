# Bench4HLS RAG 新版重排测评（2026-09-20 · hybrid + qwen3-reranker · 170 题）

## 摘要

在 Bench4HLS 全量 170 题上，用**新版 RAG（UG1399 2026.1 语料 + hybrid 检索 + Qwen3-Reranker-0.6B 重排）**跑修复阶段，与**上一轮 off 基线**做大致对比。反馈档 `functional_diagnostics`，检索策略 `diagnostic_v1`，Vitis HLS **2026.1**。

**结论：新版 RAG（hybrid + 重排）相对上一轮 off 仍接近中性（114 · 67.1% vs 115 · 67.6%，−1 题）。检索命中「真正对得上」的章节是少数，多数是词面假匹配；重排器能压低明显无关段落，但 `rag_top_k=2` 仍强制注入，且会误给语义相关但方向错误的段落高分。**

- **overall：off（上一轮）115 · 67.6% ≈ hybrid_rerank（本轮）114 · 67.1%**。注意两轮**工具链与采样不同**（见「口径差异」），属大致对比，非严格因果。
- 检索活动 70 题，其中**实际注入 16 题**（上一轮 9 题）；重排使注入面扩大，但**未转化为通过率提升**。
- 抽查注入内容：命中正确的（interface offset、ap_uint 位选）与假匹配的（undeclared 'reset' → RESET 指令、缺 ';' → FIR 库）并存。

## 评测配置

| 项 | 值 |
|---|---|
| 数据集 | Bench4HLS，全量 170 题 |
| 生成模型 / 端点 | `qwen38`，`http://127.0.0.1:8001/v1`（本地 vLLM 0.25.1-sm120-cu133） |
| 采样 | `temperature=0.7` |
| 反馈档 | `functional_diagnostics` |
| RAG policy | `agent/config/policy.rag-hybrid-rerank.json`：`rag_mode=hybrid`、`rag_reranker=qwen3-reranker-0.6b`、`rag_rerank_k=5`、`rag_top_k=2`、`rag_recall_k=30`、`rag_max_bytes=2400`、`rag_strategy=diagnostic_v1`、`rag_profile=fix_first` |
| 语料 / 注册表 | UG1399 英文 v2026.1（`rag/releases/ug1399-2026.1-en-curated.json`；fix 153 / general 1454） |
| Embedding | `Qwen3-Embedding-0.6B`，revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` |
| Reranker | `Qwen3-Reranker-0.6B`，revision `e61197ed45024b0ed8a2d74b80b4d909f1255473` |
| 停止策略 | `max_repairs=2`、`stagnation_limit=2` |
| HLS 工具 | Vitis HLS **2026.1**，part `xczu3eg-sbva484-1-e`，时钟 5 ns |
| 并行度 | 串行（1 worker） |

## 总体结果（全量 170 题）

| 指标 | off（上一轮，2025.2） | hybrid_rerank（本轮，2026.1） |
|---|---:|---:|
| parse | — | 159 · 93.5% |
| compile | 166 · 97.6% | 159 · 93.5% |
| csim/run | 116 · 68.2% | 115 · 67.6% |
| synthesis | 115 · 67.6% | 114 · 67.1% |
| **overall** | **115 · 67.6%** | **114 · 67.1%** |

> off 一列取自 `9-19-Bench4HLS-RAG-compare-v2.md`（Vitis 2025.2、temp=0.0、首稿复用）。本轮 compile 略降主要来自工具链/采样变化，不是 RAG 造成；故只看 csim/synthesis/overall 的持平即可。

## 检索注入证据

| 指标 | 上一轮（旧 RAG） | 本轮（新版+重排） |
|---|---:|---:|
| RAG 触发的题（rag_history 非空） | 70 | 70 |
| 实际注入 ≥1 条的题 | 9 | **16**（10 过 / 6 未过） |
| 低信息门跳过 | 61 | 71 |

注入状态分布（按 rag_history 条目）：`skipped 71 · injected 18 · retrieved 2 · no_reference_injected 3`。累计注入 32456 字节，均值 1803 字节/题。

## 编译错误抽查（重排后实际发给模型的内容）

### 真正命中的（少数）

**Prob045** — `error: unexpected interface offset value '0x0', expects '[slave, direct, off]'`（重排分 +3.69）
> 注入 → 「Offset and Modes of Operation」(p175)：
> ```text
> #pragma HLS INTERFACE mode=m_axi bundle=BUS_A port=out offset=direct
> #pragma HLS INTERFACE mode=m_axi bundle=BUS_B port=in1 offset=slave
> #pragma HLS INTERFACE mode=m_axi bundle=BUS_C port=in2 offset=off
> ```
> ✅ 错误信息里的 `slave/direct/off` 就是这段手册原文，教科书式命中。

**Prob041** — `no matching function for call to object of type 'ap_uint<25>'`（重排分 +2.64）
> 注入 → 「Other Class Methods, Operators, and Data Members」(p693)：
> ```text
> Rslt = Val1.range(3, 0);      // Yields: 0xF
> Val1(3,0) = Val2(3, 0);       // Yields: 0x5A
> ```
> ✅ 错误是把 `ap_uint` 当函数 `x(0)` 调用，这段给出 `(hi,lo)` 位选正确写法。

### 帮倒忙的（多数为关键词假匹配）

**Prob071** — `error: use of undeclared identifier 'reset'`（变量 `reset` 未声明；重排分反而 +3.77）
> 注入 → 「Reset」pragma 章节 (p594)：
> ```text
> #pragma HLS reset [variable=<a>] [off]
> ```
> ❌ 错误是漏声明变量，检索却因「reset」一词塞进 RESET 指令用法。语义相关所以重排给高分，方向完全错。

**Prob152** — `error: expected ';' after expression`（重排分 +2.25）
> 注入 → 「FIR Filter IP Library」(p807)：
> ```text
> #include "hls_fir.h" ... static hls::FIR<param1> fir1; fir1.run(fir_in, fir_out);
> ```
> ❌ 语法错误被「then」等词带到 FIR 滤波器库，无关。

**Prob104** — `error: 'hls.h' file not found`
> 注入 → 「Refactoring C++ Source Code for HLS」(p21)，一个 `hls::stream` 示例。
> ❌ 应补的 `hls_stream.h`/`ap_int.h` 没给，给了无关重构示例。

**Prob016** — `no matching function for call to object of type 'ap_uint<4>'`
> 注入 → 「Bit-Width Propagation」(p149) + 「Dealing with Unsupported Functions」(p134，重排 −3.4)。
> ❌ 同是 ap_uint 相关，但前者讲参数位宽传播、后者讲 ap_float，都没覆盖「`x(0)` 位选」这个真正问题。

## 关键结论

1. **新版 RAG（hybrid + 重排）整体中性**：overall 114 · 67.1% ≈ 上一轮 off 115 · 67.6%，注入从 9 题扩大到 16 题，但未带来通过率提升。
2. **检索质量两极分化**：`error:` 里带手册关键词（`offset`、`ap_uint` 位选）时能精确命中正确章节；但 `ap_uint`/`reset`/`static`/`array` 这类高频词常把检索带到「同主题但错误」的章节。
3. **重排器作用有限**：能压低明显无关段落（如 Prob147 FIR −4.9、Prob016 ap_float −3.4），但 `rag_top_k=2` 强制至少注入 2 条；且会误给「RESET 指令」这种语义相关但方向错误的段落高分（+3.77）。
4. **根因**：UG1399 是 **API 参考**，不是「编译错误 → 修复」的映射。模型缺的是识别具体 bug 模式，而不是再读一遍 API 文档。改进方向是**错误签名精确匹配**（如 `HLS 207-XXXX` 错误码、`error:` 后的模板名），而非整段手册泛匹配 + 重排。

## 口径差异（重要：这是「大致」对比，非严格因果）

| 维度 | 上一轮 off | 本轮 hybrid_rerank |
|---|---|---|
| Vitis 工具链 | 2025.2 | **2026.1** |
| 采样温度 | 0.0（贪心） | **0.7** |
| 首稿策略 | 复用 169 个有效首稿 + 共享验证 | 每条件独立生成 |

三项混杂变量导致无法把 114 vs 115 直接归因于 RAG。要拿干净因果结论，需在**同工具链、同 temp、同首稿**下重跑 off 基线。

## 环境与数据产物

- 结果 receipt：`output/bench4hls_rag/run01/hybrid_rerank/ProbXXX/attempt_000/result/result.json`
- 检索证据：`.../candidates/{n:03d}/retrieval_{input,output}.json` + `result.json` 的 `rag_history`
- 会话汇总：`.../ProbXXX/session.json`；全量 audit：`rag/.cache/audit-remote-20260920.json`
- 语料/索引/注册表：`rag/corpora/ug1399-2026.1-en-curated`、`rag/indexes/ug1399-qwen06b-2026.1-curated`、`rag/releases/ug1399-2026.1-en-curated.json`
- Git：`feat/rag @ 413e898`
