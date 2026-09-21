# RAG 证据筛查汇总（claude-remote · 9-20 重排测评）

范围：`report/design/remote_claude_rag_screening.md` 指定的 6 个案例，逐条读取 `validation_history`、`retrieval.json`、`retrieval_input.json`、`context.json`、`prompt.txt` 后复核。产物为 `rag/staging/claude-screening/bench4hls-run01-hybrid_rerank/*.json`。

## 分案例结论

| 任务 | diagnostic | 诊断完整度 | 候选 | 有效性 | 注入 | failure_stage |
|---|---|---:|---|---|---:|---|---|
| Prob045 | `unexpected interface offset ... '[slave, direct, off]'` | 2/5（无源码行/caret，但消息自带枚举） | Offset and Modes of Operation (p175) | exact_actionable (4) | true | none |
| Prob041 | `no matching function ... 'ap_uint<25>'` | 4/5 | Other Class Methods (p693) | exact_actionable (4) | true | none |
| Prob071 | `use of undeclared identifier 'reset'` | 4/5 | Reset (p594) | wrong_direction (0) | false | gate |
| Prob152 | `expected ';' after expression` | 4/5 | FIR Filter IP Library (p807) | topic_only (1) | false | gate |
| Prob104 | `'hls.h' file not found` | 4/5 | Refactoring C++ Source Code for HLS (p021) | topic_only (1) | false | gate |
| Prob016 | `no matching function ... 'ap_uint<4>'` | 4/5 | Bit-Width Propagation (p149) + Unsupported Functions (p134) | wrong_direction (0) | false | rerank |

## 汇总

- `diagnostic_truncation_cases`: **（无）**。6 例的原始 `validation_history` 均含 location/source/caret，且 `<DIAGNOSTIC>` 块完整进入 `prompt.txt`；RAG query 仅清除了路径与 caret（保留 source 行与 note），属设计上的查询归一化，不构成 extraction failure。
- `topic_only_cases`: **Prob152、Prob104**。检索命中只共享任务主题（FIR / hls::stream），未匹配诊断签名。
- `wrong_direction_cases`: **Prob071、Prob016**。分别命中 `reset` pragma、位宽传播/ap_float，方向与根因冲突。
- `exact_actionable_cases`: **Prob045、Prob041**。诊断中的枚举/调用形式与候选片段一致。
- `no_evidence_cases`: **（无）**。6 例候选文本均足以判定有效性。
- `recommended_next_change`: **`signature_gate`**。

## 关键观察

1. **诊断完整度不是瓶颈**：除 Prob045（2/5，因 interface offset 错误本就不带源码行）外，其余 5 例均为 4/5（message+location+source+caret 齐备）。但完整度高的 Prob071/152/104/016 反而全部假匹配——说明失败不在「诊断信息不足」，而在「检索没有按错误签名精确命中」。
2. **反直觉点**：完整度最低的 Prob045（2/5）却是最干净的 exact_actionable 命中，因为它的消息里直接枚举了合法值 `slave/direct/off`。可操作性来自**消息内容**（枚举、调用形式、头文件名），而非 location/caret 脚手架。
3. **两个假匹配机制**：
   - gate 型（Prob071/152/104）：`reset`、`FIR`、`hls` 等词把检索带到同主题但错误的小节，证据门没有按「错误签名」拦截；
   - rerank 型（Prob016）：重排器给 p149（位宽传播）正向分 +0.48，且 `rag_top_k=2` 强制注入，即便 p134 已被压到 −3.4。
4. **修法**：把「错误签名 → 精确章节」做成查表式 gate——`undeclared identifier`/`expected ';'`/`redefinition` 等纯 C++ 语法签名一律 abstain；`no matching function ... ap_uint<N>` 固定映射到 Ch.21 §Bit Selection `operator[]`（p692）；`offset=` 映射到 p175。签名命中不了就 abstain，而不是整段手册泛匹配 + 重排。

> 注：本复核对种子 `bench4hls-rerank-v1.jsonl` 的两处 label/score 不一致做了修正，使其对齐 `rag/annotations/README.md` 的分数↔标签锁定关系：Prob152 由 `score 0 + topic_only` 修正为 `score 1 + topic_only`；Prob016 由 `score 1 + wrong_direction` 修正为 `score 0 + wrong_direction`。证据有效性结论（哪些该注入）不变。
