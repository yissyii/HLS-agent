# Bench4HLS Workflow Skill 消融（S0–S3 · RAG-off · 2026-09-22）

## 结论（先给结论）

在 Bench4HLS 冻结 170 题的 RAG-off 条件下，按 `S0`→`S3` 逐个叠加 Workflow Skill，**最终通过率单调下降**：

| 条件 | 叠加技能 | 题目数 | 最终通过率 | 较上一档 |
| --- | --- | ---: | ---: | ---: |
| S0 | （无） | 170 | **112 / 170 = 65.9%** | — |
| S1 | + problem-contract | 170 | 89 / 170 = 52.4% | **−13.5pp** |
| S2 | + functional-selftest | 170 | 55 / 170 = 32.4% | **−20.0pp** |
| S3 | + synth-guard | 93 | 18 / 93 = 19.4% | −12.9pp（未跑满） |

**但本次运行整体 `status=invalid`，上述数字不能作为可信结论直接使用。** 主要原因有两条：

1. **协议违规**：`problem-contract` 契约在 S1 与 S2 之间对 **70 / 170 题**不一致（`contract_file_sha256` 不同），且由于契约先于首稿生成并注入上下文，连带使 **44 / 170 题**的 candidate 0（首稿）在 S1 与 S2 之间也不一致。这破坏了「temperature=0 冻结首稿、只差一个技能」的配对前提。
2. **S3 未跑满**：S3 只有 93 / 170 题（77 题缺失，其中 70 题正是因上述契约不一致触发提前返回而被跳过）。

因此本报告定位为**一次无效的消融初跑记录**：记录口径、原始数字与失效机理，供后续修正 runner 后重跑。方向性信号是「首批 Skill 在当前配置下不但无收益，反而明显拖累通过率」，但必须在消除契约/首稿不确定性的干净重跑后才能下结论。

---

## 1. 协议与条件

- 协议：`rag-off-independent-e2e-skill-ablation-v1`（RAG-off 独立端到端 skill 消融）
- 数据集：Bench4HLS 冻结 benchmark replay，170 题（Prob001–Prob170），`dataset_source_commit = 7fac5b356b0383e9463995e2c6b13a5fee27a62d`
- 反馈档：`functional_diagnostics`
- 全条件统一：`rag_enabled=false`、旧规则 `skills_enabled=false`、`max_repairs=2`、`stagnation_limit=2`、`synth_guard_mode=observe`（不做综合门禁）

| 条件 | `problem_contract_enabled` | `functional_selftest_enabled` | `synth_guard_enabled` | 生效技能 |
| --- | --- | --- | --- | --- |
| S0 | ✗ | ✗ | ✗ | （无） |
| S1 | ✓ | ✗ | ✗ | problem-contract |
| S2 | ✓ | ✓ | ✗ | + functional-selftest |
| S3 | ✓ | ✓ | ✓ | + synth-guard（observe） |

策略文件：`agent/config/policy.skill-s{0,1,2,3}.json`，运行时冻结并记录 `policy_sha256`、快照哈希。技能入口见 [skill/README.md](../../skill/README.md)。

## 2. 运行环境

- 模型：本地 vLLM `qwen38`（`http://127.0.0.1:8001/v1`），`temperature=0.0`，`enable_thinking=false`，`context_tokens=16384`，`max_tokens=4096`，`timeout_seconds=180`。模型版本无 revision 证据，仅服务别名 `qwen38`。
- 工具链：Vitis/Vivado 2026.1，`part=xczu3eg-sbva484-1-e`，`clock_ns=5`，csim 120s / synthesis 300s / total 600s。
- 并行：`workers=4`（本次运行的 key 变更，见第 6 节失效机理）。
- 运行窗口：`2026-09-22T10:44:57Z` → `12:26:06Z`，墙钟约 6068s（≈1.7h）。
- API 请求：全程记录 **1189** 次，`request_outcomes_unknown=0`。

## 3. 总结果

| 条件 | 题数 | 初稿通过 | 最终通过 | 修复净增（recoveries） |
| --- | ---: | ---: | ---: | ---: |
| S0 | 170 | 99（58.2%） | **112（65.9%）** | +13 |
| S1 | 170 | 86（50.6%） | 89（52.4%） | +3 |
| S2 | 170 | 55（32.4%） | 55（32.4%） | 0 |
| S3 | 93 | 18（19.4%） | 18（19.4%） | 0 |

关键观察：S0（无技能）能通过有限修复循环净增 **13** 题（99→112），S1 只剩 +3，S2/S3 归零。叠加技能越多，修复循环越「修不动」，说明技能不是单纯无效，而是在**挤压/阻断修复**。

### 分级验证（最终）

| 条件 | compile | run（csim） | synthesize |
| --- | ---: | ---: | ---: |
| S0 | 165 | 113 | 112 |
| S1 | 120 | 90 | 89 |
| S2 | 64 | 55 | 55 |
| S3 | 20 | 18 | 18 |

（`run`/`synthesize` 是 `compile` 通过后的窄化子集，最终通过率 = synthesize 通过数。）

## 4. 配对差分（paired deltas）

| 对比 | 赢 | 输 | 平 | 净变化 |
| --- | ---: | ---: | ---: | --- |
| S1 − S0 | 6 | 29 | 135 | −23 |
| S2 − S1 | 2 | 36 | 132 | −34 |
| S3 − S2（93 题重叠） | 2 | 7 | 84 | −5 |
| S3 − S0（93 题重叠） | 2 | 40 | 51 | −38 |

逐档叠加时「输」远多于「赢」：S1 相比 S0 净 −23，S2 相比 S1 净 −34。S3 相对 S2 的 −5 因重叠样本只有 93 题且 S2 本身已很低，参考意义有限。

## 5. 开销

| 条件 | 平均 model 请求 | 平均 workflow 请求 | 平均总 token | 平均耗时（s） |
| --- | ---: | ---: | ---: | ---: |
| S0 | 1.494 | 0.0 | 2378.5 | 28.05 |
| S1 | 1.918 | 1.0 | 4551.5 | 38.37 |
| S2 | 2.500 | 1.741 | 6514.9 | 52.17 |
| S3 | 1.978 | 1.527 | 5055.8 | 42.40 |

叠加技能带来明显开销：S2 相比 S0 平均 token 约 ×2.7、耗时约 ×1.9。这些开销本应换得修复收益，但实际通过率反而下降。

## 6. 技能触发与失败模式

| 条件 | contract_ready | contract_failures | selftest_runs | selftest_failures | synth_guard_runs | synth_guard_findings |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| S1 | 124 | 43 | — | — | — | — |
| S2 | 126 | 40 | 107 | 41 | — | — |
| S3 | 49 | 41 | 36 | 16 | 18 | 12 |

失败类别分布（`receipt.category`）：

| 类别 | S0 | S1 | S2 | S3 |
| --- | ---: | ---: | ---: | ---: |
| `problem_contract_ambiguous` | 0 | 24 | 23 | 24 |
| `selftest_invalid` | 0 | 0 | 37 | 23 |
| `workflow_output_error` | 0 | 19 | 17 | 17 |
| `functional_or_runtime_error` | 15 | 4 | 3 | 0 |
| `generation_incomplete` | 3 | 4 | 9 | 6 |
| `context_budget_exceeded` | 2 | 9 | 5 | 0 |

即：S1 的主要新失败是 `problem_contract_ambiguous`（24）+ `workflow_output_error`（19）；S2 的主要新失败是 `selftest_invalid`（37）——自测在 107 次运行中判「无效」41 次，成为最大单项退化来源。S3 的 `synth_guard` 在 18 次触发中报 12 个发现，但仅为 observe，未直接作门禁。

## 7. 协议违规与失效机理（重点）

本次运行 `summary.status = "invalid"`，`protocol_error = "Prob001: contract differs between S1 and S2"`。逐题核对后确认不是孤例：

1. **契约不一致 70 / 170**：`problem-contract` 产物 `contract.json` 的 `contract_file_sha256` 在 S1 与 S2 之间不同。抽查 Prob001 显示，两边的 `request_sha256` **完全相同**（模型收到同一请求），但内容字段 `boundaries` / `risk_points` 不同——说明这是**模型在 temperature=0 下仍存在生成不确定性**，而非上下文污染。
2. **首稿不一致 44 / 170**：`candidate0_sha256`（首稿源码哈希）在 S1 与 S2 之间不同。契约先于首稿生成并注入上下文，故契约不确定性向下游级联到首稿，进一步破坏配对前提。
3. **S3 缺 77 题**：runner 在 S2 阶段检测到契约/首稿不一致即对当前题 `return`，跳过其 S3。因此 93 题的 S3 不是「跑满后的抽样」，而是「被提前中止后的残余」，与 S0–S2 不可直接比。

**可能机理（待验证）**：`temperature=0` 依赖 vLLM 采样在单请求顺序执行下才稳定；本次新增 `--workers 4` 并行，同批多个请求在 batched inference 下可能引入浮点/批序不确定性，从而打破「冻结首稿」假设。该假设正是协议里 `_run_task` 对 candidate0 / contract 做 S1 基准比对的前提。**修正方向**：先以 `--workers 1` 顺序重跑 S0–S3 验证是否存在同源不确定性；若仍存在，需进一步固定 vLLM 采样（如 `seed`/`top_p`/禁用并行批）或接受契约/首稿在跨条件比较中不可配对。

## 8. 复现

```bash
python tools/run_skill_compare.py \
  --all --conditions S0,S1,S2,S3 \
  --workers 4 \
  --output output/remote-skill-ablation/full-5335e89-par4
```

- 配置默认 `serve/runtime.rag-eval.json`（qwen38 + temperature=0），技能目录默认 `skill/`。
- 产物根：`output/remote-skill-ablation/full-5335e89-par4/`；有效轮 `attempt_000`，结果在 `.../attempt_000/result/`（`summary.json`、`runs/{ProbXXX}/{S0..S3}/`、`policies/S{0..3}.json`、`workflow_skills/`）。
- 会话：`.../session.json`（`status=completed_with_failures`，`valid_attempt=attempt_000`，exit_code 1）。
- 逐题审计证据在 `.../runs/{ProbXXX}/{cond}/`（`result.json`、`events.jsonl`、`workflow/problem-contract/contract.json`、`workflow/functional-selftest/bundle.json` 等）。

## 9. 后续

- **先修 runner / 采样确定性**，再决定是否重跑；当前结果不构成「Skill 有害」的正式证据，只能作为「需要警惕」的方向性信号。
- 若干净重跑后仍复现「叠加技能单调降低通过率」，需回到 `functional-selftest` 的 `selftest_invalid`（S2 最大退化源）与 `problem-contract` 的 `ambiguous`（S1 最大退化源）逐条归因，判断是 prompt/验收条件问题还是技能设计本身与当前修复循环冲突。
- S3 的 `synth-guard` 目前 observe-only 且样本不足，暂无法评估其独立贡献。
