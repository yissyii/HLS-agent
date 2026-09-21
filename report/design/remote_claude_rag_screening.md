# 远程 Claude 的 RAG 证据筛查计划

目标是判断 9-20 重排测评中的假匹配究竟来自诊断信息不足、候选召回、重排、证据门还是语料缺口。本次只做注释和分析，不修改活动 corpus、release、index 或 policy。

## 运行范围

优先检查以下 6 个案例：

| 任务 | 报告判断 | 首要问题 |
| --- | --- | --- |
| Prob045 | 正确命中 | 建立 exact_actionable 正例 |
| Prob041 | 正确命中 | 建立 exact_actionable 正例 |
| Prob071 | 错误方向 | 区分用户变量 `reset` 与 HLS reset pragma |
| Prob152 | 主题假匹配 | 判断 `expected ';'` 是否缺少源码上下文 |
| Prob104 | 主题相关但不可执行 | 判断是否真的缺少 `hls_stream.h` 证据 |
| Prob016 | 主题相近但方向错误 | 判断 `ap_uint` 调用形式是否出现在诊断或源码行 |

案例初始记录在 `rag/annotations/bench4hls-rerank-v1.jsonl`。远程 Claude 只能补充和修正 `rag/staging/` 下的复核结果，不得直接编辑该发布种子或 `rag/corpora/`。

## 每个案例的注释顺序

### 1. 诊断完整度

在远程 run 目录中找到该任务最后一次触发 RAG 的 attempt，读取：

```text
candidates/NNN/retrieval_input.json
candidates/NNN/retrieval.json
candidates/NNN/context.json
candidates/NNN/prompt.txt
candidates/NNN/validation.json
result.json
events.jsonl
```

只注释以下 JSON 路径或文本块：

```text
result.json.validation_history[*].feedback
result.json.validation_history[*].outcome.entries
candidates/NNN/retrieval_input.json.query
candidates/NNN/retrieval_input.json.query_plan.annotation
candidates/NNN/retrieval.json.diagnostic_annotation
candidates/NNN/prompt.txt.<DIAGNOSTIC>...</DIAGNOSTIC>
```

填写：

```text
diagnostic_quality_score: 0..5
has_location: true/false
has_source_line: true/false
has_caret: true/false
has_note: true/false
feedback_trimmed: true/false
query_trimmed: true/false
source_window_needed: true/false
```

不要把工作区绝对路径、时间戳和 attempt 编号当成检索证据。它们只用于回溯。

### 2. 候选证据

在 `retrieval.json` 中逐条检查：

```text
retrieved_ids
selection_audit[*]
hits[*].record.title
hits[*].record.text
hits[*].record.source
hits[*].rerank_score
injected_ids
injected_bytes
```

在 `context.json` 中确认真正注入的 `rag_candidate_ids`，再到 `prompt.txt` 的 `<REFERENCE_MATERIAL>` 中确认模型实际看到的完整片段。只召回但未注入的候选不能标记为模型误用。

每个候选使用以下标签：

```text
exact_actionable  错误签名和修复动作都匹配
applicable_rule   适用，但需要模型结合源码完成修改
partial           只有部分限制匹配
topic_only        只共享主题或词汇
wrong_direction   会把模型引向另一个错误族
no_evidence       文本没有足够信息判断
```

给 `evidence_usefulness_score` 赋 0～4 分，规则见 `rag/annotations/README.md`。同时记录：

```text
failure_stage: extraction | recall | gate | rerank | judge | corpus_gap | none
should_inject: true | false | null
reason: 一句话说明依据
```

### 3. 诊断截断判断

只有满足以下条件，才标记 `failure_stage=extraction`：

1. 原始 `validation_history` 中存在文件/行号、源码行、caret 或 note；
2. 这些内容在 `prompt.txt` 或 `retrieval_input.json` 中确实消失；
3. 缺失内容足以改变错误族判断。

如果源码行已经进入修复 prompt，但 RAG query 清除了路径和 caret，标记为设计上的查询归一化，不标记为 extraction failure。路径和行号本身不是手册检索词；应判断是否需要从当前候选源码截取出错行前后 3～5 行。

### 4. 假匹配判断

以下情况标记 `wrong_direction`：

- `use of undeclared identifier 'reset'` 命中 reset pragma；
- 普通语法错误命中 FIR、FFT、array partition 等具体库或优化章节；
- `ap_uint` 类型错误命中位宽传播，但源码实际是位选择调用；
- 候选只与题目主题相关，没有解释诊断中的操作、参数或错误码。

以下情况才可标记 `exact_actionable`：

- 诊断中的 HLS/SYNCHK 错误码或参数枚举在资料中出现；
- 诊断中的 API、头文件、操作符或 pragma 形式与资料一致；
- 资料给出了当前错误所需的限制、正确写法或替代写法；
- 适用条件没有与当前任务冲突。

## 注释输出格式

每个案例写一个 JSON 文件到：

```text
rag/staging/claude-screening/<run_id>/<task>.json
```

推荐格式：

```json
{
  "schema_version": 1,
  "task": "Prob071",
  "attempt": 1,
  "source_run": "output/...",
  "diagnostic_quality_score": 4,
  "diagnostic_quality_status": "reviewed",
  "has_location": true,
  "has_source_line": true,
  "has_caret": true,
  "has_note": false,
  "feedback_trimmed": false,
  "query_trimmed": false,
  "source_window_needed": true,
  "candidates": [
    {
      "id": "ug1399-2026.1-en-s...",
      "title": "Reset",
      "evidence_usefulness_score": 0,
      "retrieval_label": "wrong_direction",
      "should_inject": false,
      "failure_stage": "gate",
      "reason": "reset is a user identifier, not an HLS reset pragma"
    }
  ],
  "reviewer": "claude-remote",
  "reviewed_at": "2026-09-21T00:00:00Z",
  "status": "pending_human_review"
}
```

如果文件不存在、内容为空或无法确定，应保留 `null`，不要猜测。输出中不得复制隐藏测试答案、完整 testbench 或模型权重；只保留已授权反馈和候选手册片段的 ID/短摘要。

## 审查结束条件

六个案例全部有诊断完整度评分和候选标签后，生成一份汇总：

```text
diagnostic_truncation_cases
topic_only_cases
wrong_direction_cases
exact_actionable_cases
no_evidence_cases
recommended_next_change
```

`recommended_next_change` 只能选择一个主要原因：`diagnostics`、`signature_gate`、`reranker`、`judge` 或 `corpus`。在汇总完成前不要删除 UG1399 条目、修改 release 或把候选修复规则发布到活动库。
