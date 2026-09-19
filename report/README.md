# 报告与证据索引

集中保存设计报告、复现说明、模型选择依据和重要模型协作记录；baseline 与 agent 的实验结果分别报告。2026-09-18 完成分类归档，保留历史报告文件名和各轮实验的原始口径。

先看 [项目结构思维导图](design/project_structure.md) 和 [Agent 与裸基线入口流程图](design/agent_flow.md)。Obsidian 原生画布：[项目结构 Canvas](project-structure.canvas) · [Agent 流程 Canvas](agent-flow.canvas)。使用命令见 [项目 README](../README.md)。

## 分类与归档规则

| 分类 | 目录 | 收录内容 |
| --- | --- | --- |
| 设计与实现 | `design/` | 架构、模块职责、实现验证、项目状态和结构图 |
| 复现说明 | `reproducibility/` | 基线协议、环境适配、复现步骤与验证范围 |
| 模型选择依据 | `model_selection/` | 模型版本、方案对照与选择证据；目前收录检索方案实测 |
| 评测结果 | `evaluations/` | 各轮 baseline / agent 对照，按日期、题目范围及实验条件区分 |
| 重要模型协作记录 | 暂无独立文件 | 后续记录关键交互、决策及验证证据；有内容时建立 `collaboration/` |

模块 README、模型声明、开发任务清单留在各自模块。论文阅读、讲座笔记和文献下载清单继续放在上级 `docs/`。原始运行日志、候选、数据集和模型权重不移入本目录；报告通过路径引用证据。

## 设计与实现

| 文档 | 内容与状态 |
| --- | --- |
| [项目结构思维导图](design/project_structure.md) | 工作区、框架、知识资料、报告和本地产物之间的关系 |
| [Agent 与裸基线入口流程图](design/agent_flow.md) | 两条入口、模块职责、修复循环与研发评测外层；按 2026-09-18 代码核对 |
| [HLS Agent 设计与工作原理](design/design.md) | 主控制器、输入边界、上下文、候选选择和结果语义 |
| [Agent 首版实现与验证](design/agent_implementation.md) | 2026-09-16 的合同测试与实际 Vitis 最小闭环记录 |
| [项目定位与实现状态](design/instruction.md) | 按 2026-09-18 代码更新的能力、配置、使用方式与剩余工作 |

## 复现说明

| 文档 | 内容 |
| --- | --- |
| [基线入口与数据保留](reproducibility/baseline_protocol.md) | 严格裸基线、同配置配对、冻结数据和历史结果限制 |
| [环境适配与闭环验证：2026-09-13](reproducibility/zcomp-hls-harness-环境适配与闭环验证-20260913.md) | Windows 环境、本地配置覆盖和三项验证记录 |

## 模型与检索方案依据

[UG1399 2025.2 检索方案实测](model_selection/UG1399-2025.2.md) 比较 BM25、Qwen 向量和混合检索，记录嵌入模型版本、语料构建、耗时与复现证据。它属于检索方案选择依据，不能代表生成模型选型已完成，也不能证明 Agent 修复成功率提高。

生成模型的正式声明仍在 [model/MODEL.md](../model/MODEL.md)，目前为待填写模板。原始核验与逐题检索 JSON 保留在 `rag/reports/`，报告中的链接指向这些原始文件。

## baseline / agent 评测

| 日期 | 范围与条件 | 报告 |
| --- | --- | --- |
| 2026-09-16 | Hard 50 题 | [Hard 子集](evaluations/9-16-Bench4HLS-BLandAgent-Hard.md) |
| 2026-09-16 | 全量 170 题，含故障案例复测修正 | [全量评测](evaluations/9-16-Bench4HLS-BLandAgent-ALL.md) |
| 2026-09-17 | 全量 170 题，沙箱复跑 | [沙箱评测](evaluations/9-17-Bench4HLS-BLandAgent-ALL-SandBox.md) |
| 2026-09-17 | 全量 170 题，更新源码提取逻辑后重跑 | [提取更新后的评测](evaluations/9-17-Bench4HLS-BLandAgent-ALL-SandBox-ExtractUpdated.md) |
| 2026-09-19 | 全量 170 题，`compiler_diagnostics` 反馈档 | [compiler_diagnostics 评测](evaluations/9-19-Bench4HLS-BLandAgent-ALL-compiler_diagnostics.md) |

各轮独立保留，不将后续结果覆盖到旧轮次。Hard 子集与全量题目重叠，不能合计为独立样本。报告中的历史配置和分数保留，并补充后续进展与适用时间；当前实现说明按源码更新。本次未重新评测或重算结果。

新增报告至少分别记录 baseline 和 agent 的：

- 模型名称与版本或 revision、模型服务及采样/上下文配置；只有服务别名时明确说明版本证据不足。
- 数据集版本、任务划分、题目数量与公开材料范围。
- API 请求或推理次数，区分初始生成、内部修复与外层作废重跑。
- 耗时及计时边界，区分生成、验证、修复与总时间。
- 验证级别与结果：解析、编译、运行、综合；未执行或证据不足时明确标注。
- 运行编号、产物位置、失败分类和可复现命令。研发会话按 `session.json.valid_attempt` 选择有效轮。

历史报告缺失的字段保留为未知，不根据文件名或当前配置补造历史信息。

## 相关资料

- [Agent 使用说明](../agent/README.md)、[研发评测生命周期](../local_eval/README.md)。
- [RAG 使用说明](../rag/README.md)、[RAG 开发任务](../rag/TASKS.md)。
- [论文共读记录](zcomp/docs/ReadReference/README.md)、[AMD 讲座整理笔记](AMD讲座_整理笔记.md)、[参考文献下载清单](download-manifest.md)。

## 本次迁移对照

以下原路径以工作区 `ADMCmpt/` 为起点；新路径以 `zcomp/report/` 为起点。

| 原位置 | 新位置 |
| --- | --- |
| `zcomp/agent/design.md` | `design/design.md` |
| `zcomp/report/agent_implementation.md` | `design/agent_implementation.md` |
| `zcomp/instruction.md` | `design/instruction.md` |
| `zcomp/report/baseline_protocol.md` | `reproducibility/baseline_protocol.md` |
| `record/zcomp-hls-harness-环境适配与闭环验证-20260913.md` | `reproducibility/zcomp-hls-harness-环境适配与闭环验证-20260913.md` |
| `zcomp/rag/reports/UG1399-2025.2.md` | `model_selection/UG1399-2025.2.md` |
| `zcomp/record/` 下四份 Bench4HLS Markdown | `evaluations/` 下对应同名文件 |
| `zcomp/record/9-19-Bench4HLS-BLandAgent-ALL-compiler_diagnostics.md` | `evaluations/9-19-Bench4HLS-BLandAgent-ALL-compiler_diagnostics.md` |
