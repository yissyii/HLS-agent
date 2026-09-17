# 基线入口与数据保留

## 入口

```bash
bash run_baseline.sh /path/to/problem.txt output/baseline_run --config output/generation_only_20260914_2344/config.json
```

第二个参数是新输出目录。每轮裸基线原样传入题目 UTF-8 文本，只发送一次生成请求；不追加指令、头文件或示例，不调用 `/tokenize`、EDA、智能体或技能。输出截断保留为失败，不续写。完整响应由共享的 `serve/code.py` 提取源码，原始响应保留。旧 `run_baseline.ps1` 是原有“输出文件”接口，不是这个新接口。

研发环境的 [评测管理模块](../local_eval/README.md) 默认包裹整个评测入口：遇到网络故障时作废整轮并重跑；显式输出目录是会话目录，应通过 `session.json.valid_attempt` 定位该轮的 `result/`。从提交副本移除 `local_eval/` 后恢复单轮行为。

2026-09-17 起，提取规则允许 Markdown 代码块前后有解释文字。多个 C/C++ 或具有源码特征的无标签代码块，按结构闭合、函数定义、测试台特征及支持代码等固定规则排序，选取最可能完整的一个；同分选最先出现者。裸基线不额外读取任务清单来选择代码。评分只看响应结构，不编译候选、不拼接代码、不增加模型请求；细节见 [设计文档的响应提取说明](../agent/design.md#64-响应提取与多代码块选择)。

`result.json.source_extraction_details` 保存规则版本、每个代码块的评分、排除原因和选中编号，便于复核。该规则不改变 `finish_reason != stop` 的失败处理。历史评测记录继续按原提取规则解释，不能直接据此推算新增通过数。

输出保存 `problem.txt`、`request.json`、`config.json`、`response.txt`（收到时）、响应元数据、`result.json`，正常完整生成时保存 `candidate.cpp`。记录输入／配置／代码哈希、运行编号、请求次数、token、结束原因和时间。已有输出目录拒绝覆盖；失败样本不剔除。

默认配置仍为项目原有 `serve/runtime.json`，不会自动改为历史实验参数。复现实验必须显式指定对应配置。支持 HTTPS 服务，以及最终容器中的 loopback HTTP 服务；当前开发配置仍指向外部端点，未冒充断网部署已完成。此修改未调整任何已冻结模型参数。

## 与智能体同次产出

```bash
bash run_paired.sh /path/to/problem.txt output/paired_run --config path/to/shared_config.json --agent-entry ./run.sh
```

配对启动器在同一调用中运行新基线及智能体，共享题目、完整配置快照和 run_id。基线失败不会导致跳过智能体。智能体入口协议为：

```text
run.sh <problem_file> <output_directory> --config <snapshot> --run-id <id>
```

智能体须使用共享配置，并在 `<output_directory>/result.json` 中记录实际使用的 `run_id`、`problem_sha256`、`config_sha256`；配置哈希为键排序、紧凑 JSON 的 UTF-8 SHA256，与 `serve/baseline_entry.py` 一致。配对启动器校验回执及共享文件未变，输出 `pair.json`。回执核对不是对智能体内部行为的强制证明，最终还应审查实际请求记录和服务配置。

2026-09-16 已接入 `run.sh` 和独立 agent；配对入口新增 `--task-manifest`、`--policy`、`--skills-dir`、`--cpu-only`。公开验证材料、策略与启用的技能包在基线运行前复制为快照，运行后核对材料哈希；回执还校验策略和技能包哈希。Windows 标准入口直接运行相同 Python 模块，无需 Bash；自定义 shell 入口仍依赖 Bash。agent 受总预算和外层看门狗约束。

配对成功只表示两次运行的输入与配置对得上。agent 退出 0 表示交付了候选，仍需看 `validation_status`、`validation_scope` 和独立评测结果；基础设施错误返回非零。没有公开验证清单时，agent 不声称功能验证通过。pass@5 的外层采样规则以赛事细则为准，内部修复次数不计作独立采样次数。

## 现有资料

`tools/preserve_baseline_evidence.py` 将历史生成批次、重试记录和已下载的评测包存为独立压缩包，并逐文件校验 SHA256，原文件保留。索引明确标记为历史开发结果：输入曾含包装／头文件、存在重试及单题提示干预，且没有同期智能体结果，不能改标为赛事正式基线。

历史累计保存 121/122 份生成代码；独立评测为解析 107、编译 106、公开测试校验 59、综合 49。综合另有 4 项明确错误、6 项固定预算超时；1 项参考校验异常。详细结果位于 `output/frozen_eval_121_20260915/remote_results/audited_summary.json`。不以该统计冒充官方 pass@1/pass@5。

本题集冻结：保存和评测仅为报告提供证据，不依据其失败结果调整模型、提示词、技能或候选代码。本次入口测试使用本地模拟服务和人工小样本，不调用真实模型或重新生成测试集。
