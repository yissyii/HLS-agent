# Bench4HLS fix cards 对照评测（2026-09-21）

## 摘要

在 Bench4HLS 全量 170 题上，用**首批 18 张 UG1399 高精度修复卡（fix cards）**跑修复阶段，与**完整 UG1399 general release** 做配对对照（复用同一批冻结首稿）。结论：**fix cards 相对 general 无修复收益，反而少恢复 1 题**。

- general hybrid：overall 121/170（71.2%），恢复 17/62（27.4%）
- fix-rerank hybrid：overall 120/170（70.6%），恢复 16/62（25.8%）
- 18 张卡里**只有 3 张被召回/注入**（ap_uint 位选、interface offset、ap-int header），其余 15 张从未命中任何失败首稿的诊断。

**根因**：fix cards 覆盖面过窄（18 张卡只对应 3 个真实出现的错误族），且这 3 个错误族在 general 全量里同样能被命中；因此 fix cards 没有提供增量恢复，general 反而靠更大覆盖面多救回 1 题（Prob154）。

## 评测配置

| 项 | 值 |
|---|---|
| 数据集 | Bench4HLS 全量 170 题 |
| 生成模型 | `qwen38` @ `http://127.0.0.1:8001/v1`（本地 vLLM） |
| 采样 | `temperature=0.0`（冻结首稿） |
| HLS 工具 | Vitis HLS **2026.1**，part `xczu3eg-sbva484-1-e`，clock 5 ns |
| 反馈策略 | `functional_diagnostics` |
| 修复预算 | `max_repairs=2`、`stagnation_limit=2` |
| 检索 | `rag_mode=hybrid`、`rag_top_k=2`、`rag_max_bytes=2400`、`rag_strategy=diagnostic_v1` |
| general policy | `policy.rag-hybrid`，`rag_profile=fix_first`，reranker=off |
| fix-rerank policy | `policy.rag-hybrid-rerank`，`rag_profile=all`，reranker=`Qwen3-Reranker-0.6B` |
| 并行度 | 串行（workers=1） |

> 本轮按效率要求**跳过了「fix 无重排」组**，只测 general（完整 UG1399）与 fix-rerank（18 张卡 + 重排）。

## 冻结与产物指纹

- `target_commit`：`9a0e99c703c6c402cac5bf0b45d1f5b67dc1dc56`（评估起始；本轮又在此之上做了 3 处评测脚手架修复，见「附录：脚手架修复」）
- general release：`ug1399-2026.1-en-curated-reference-1`（records `2fa0452b`，index `c621f647`）
- fix-cards release：`ug1399-2026.1-fix-cards-v1`（records `9a0c9e4d`，index `57b9f4f7`）
- Embedding：`Qwen3-Embedding-0.6B`；Reranker：`Qwen3-Reranker-0.6B`（均本地文件加载）

## 结果

### 通过率与恢复（overall = csim 通过 ∧ synthesis 通过）

| 组 | off | bm25 | hybrid | hybrid 恢复 | hybrid 相对 off 胜/负/平 |
|---|---:|---:|---:|---:|---|
| general（完整 UG1399） | 120 | 121 | **121** | **17/62 (27.4%)** | 1 胜 / 0 负 |
| fix-rerank（18 卡 + 重排） | 120 | 120 | **120** | **16/62 (25.8%)** | 0 胜 / 0 负 |

- 冻结首稿池：62 个失败首稿（`stop_reason=repair_budget_exhausted`），另 104 首稿一次通过、4 首稿生成不完整（Prob067/109/149/155，两轮一致，从对比中排除）。
- general hybrid 相对 off 多恢复 **Prob154**；fix-rerank hybrid 相对 off **零增量**。
- 三组 `off` 完全一致（120），证明首稿冻结与复用正确（fix-rerank 的 170 个 draft 全部 `reused_from` 有值、`source_sha256` 与 general 一致）。

### 检索证据（fix-rerank）

- `rag_history` 状态：`skipped 124` · `injected 14` · `no_reference_injected 18`
- `no_reference_reason`：`no_candidate_passed_evidence_gate 16`（其余 2 为无候选）
- **18 张卡中只有 3 张被注入**：
  | 卡片 | 注入次数 |
  |---|---:|
  | `fix-ug1399-2026.1-ap-uint-bit-selection`（ap_uint 位选） | 10 |
  | `fix-ug1399-2026.1-interface-offset-vitis-kernel`（m_axi offset） | 2 |
  | `fix-ug1399-2026.1-ap-int-header`（补 ap_int.h） | 2 |

  其余 15 张卡（recursion、dynamic memory、STL、DATA_PACK、ap_bus、hls_stream、dataflow、pipeline、array_partition 等）**从未被召回**——对应错误族在本轮 62 个失败首稿中没有出现。

## 关键结论

1. **fix cards 未带来修复增益**：hybrid 恢复 16/62，与 off 基线持平；general 的 hybrid 反而多恢复 1 题（Prob154）。18 张高精度卡的可控性没有转化为通过率。
2. **覆盖面是硬约束**：18 张卡只命中 3 个错误族，而这 3 族（ap_uint 位选、interface offset、缺 ap_int.h）在 general 全量 UG1399 里同样有对应章节（p692 Bit Selection、p175 Offset、p680 头文件），故 fix cards 没有增量。
3. **错误族与卡的错配**：62 个失败首稿里大量是**纯 C/C++ 语法错误、功能逻辑错误、或 UG1399 未覆盖的错误**（如 Verilog 字面量 `2'b`、undeclared identifier、redefinition、数组越界初始化），这些既不在 18 张卡内，也不在 general 检索的精确命中范围内——印证了 9-20 报告的判断：UG1399 是 API 参考，不是「编译错误→修复」映射。
4. **证据门行为正确**：16 次 `no_candidate_passed_evidence_gate` 说明重排后仍无候选通过证据门时系统会正确 abstain，没有为凑 Top-K 强制注入（符合 smoke 通过条件）。

## 建议

- 本轮 fix cards 的**签名精确匹配方向是对的**（签名门+证据门都能正确 abstain），但**覆盖不足**。要取得修复增益，需要把卡从 18 张扩展到**失败首稿中真实高频的错误族**（ap_uint 位选/范围选、Verilog 字面量、undeclared/redefinition、头文件缺失等），并明确「纯 C/C++ 语法错误不检索、直接由模型基础能力修复」。
- 本轮未对卡片 `validation.status` 做任何升级（按计划约定）。

## 附录：评测脚手架修复（本轮发现并提交）

| 提交/改动 | 问题 | 修复 |
|---|---|---|
| `9a0e99c` | `--drafts-from` 复用路径读 `off`（generation=skipped）导致从不复用 | 改为读 `draft`（generation=passed） |
| （随报告提交） | 复用后对 170 首稿重跑 Vitis csim+synth（~1h 冗余） | 改为直接从 general 批次 `draft/result.json` 抽取 `validation_history` |
| （随报告提交） | 复用 draft 行缺 `stop_reason`，`summarize()` 算不出 recovery（`recovery_denominator=0`） | 从 general 批次回填 `stop_reason`/`checks` |

> 本轮报告中的 recovery 数值系从 `results` 逐题 `overall` 与 general 的 `failed_drafts` 手工计算（因 fix-rerank 运行时上述第 3 处修复尚未生效，`summary.json` 的 recovery 字段为 0/0）；修复后后续运行可直接从 `summary.json` 读取。
