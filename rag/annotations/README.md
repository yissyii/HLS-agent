# RAG 证据标注表

这些记录用于标注“诊断是否足够具体”和“候选资料是否值得注入”。它们是评测分析数据，不是活动知识库；未通过审核的记录不得进入 `rag/releases/`。

## 评分规则

### 诊断完整度 `diagnostic_quality_score`（0～5）

| 分数 | 条件 |
| ---: | --- |
| 1 | 有一条明确错误/异常消息 |
| 2 | 有错误码或明确错误模板 |
| 3 | 有文件、行号或列号 |
| 4 | 有对应源码行 |
| 5 | 有 caret、note 或可定位的补充上下文 |

分数是累加项。`diagnostic_quality_score` 不能代表修复难度，也不能代表 RAG 应该命中。

### 资料有效性 `evidence_usefulness_score`（0～4）

| 分数 | 标签 | 含义 |
| ---: | --- | --- |
| 0 | `wrong_direction` | 与根因方向冲突或会诱导错误修改 |
| 1 | `topic_only` | 只共享词汇/主题，没有可执行修复信息 |
| 2 | `partial` | 覆盖一部分约束，但缺少关键条件 |
| 3 | `applicable_rule` | 规则适用，能指导修改，但没有完整模板 |
| 4 | `exact_actionable` | 错误签名、构造和修复动作都匹配 |

### 注入结论

`should_inject=true` 只允许出现在分数 3 或 4，且没有明显排除条件的记录。`topic_only` 和 `wrong_direction` 必须为 `false`。无法判断时填写 `null`，交给远程筛查继续处理。

## 自动字段和人工字段

Agent 每轮在 `retrieval.json` 中写入 `diagnostic_annotation`，候选在 `selection_audit[].annotation` 中写入临时词项支持分数。它们的 `status` 是 `provisional`，不是人工真值。

人工标注至少填写：

```text
diagnostic_quality_score
diagnostic_quality_status
evidence_usefulness_score
retrieval_label
should_inject
failure_stage
reason
reviewer
reviewed_at
```

`failure_stage` 使用 `extraction`、`recall`、`gate`、`rerank`、`judge`、`corpus_gap` 或 `none`。如果资料没有进入上下文，不能把它标为“模型误用”；应记录为“候选未注入”。

## 当前案例种子

`bench4hls-rerank-v1.jsonl` 从 `report/evaluations/9-20-Bench4HLS-RAG-rerank.md` 的人工抽查生成。它们已标出初步的资料有效性，但诊断完整度和远程产物路径仍需 Claude 根据 `retrieval_input.json`、`retrieval.json`、`context.json`、`prompt.txt` 复核。
