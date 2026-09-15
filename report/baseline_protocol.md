# 基线入口与数据保留

## 入口

```bash
bash run_baseline.sh /path/to/problem.txt output/baseline_run --config output/generation_only_20260914_2344/config.json
```

第二个参数是新输出目录。此入口原样传入题目 UTF-8 文本，只发送一次生成请求；不追加指令、头文件或示例，不调用 `/tokenize`、EDA、智能体或技能。网络错误及输出截断均保留为失败，不重试、不续写。仅对完整响应进行固定的单层代码围栏剥离，原始响应保留。旧 `run_baseline.ps1` 是原有“输出文件”接口，不是这个新接口。

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

当前本地项目没有可用的 `run.sh`，因此配对运行会在模型请求前明确失败；本次只建立基线和对接入口，未声称完整智能体方案已经完成。实际 `run.sh` 接入后，才能产生满足“同次运行”的正式增益比较。pass@5 的外层采样规则以赛事细则为准，本入口内部不做五次重试。

## 现有资料

`tools/preserve_baseline_evidence.py` 将历史生成批次、重试记录和已下载的评测包存为独立压缩包，并逐文件校验 SHA256，原文件保留。索引明确标记为历史开发结果：输入曾含包装／头文件、存在重试及单题提示干预，且没有同期智能体结果，不能改标为赛事正式基线。

历史累计保存 121/122 份生成代码；独立评测为解析 107、编译 106、公开测试校验 59、综合 49。综合另有 4 项明确错误、6 项固定预算超时；1 项参考校验异常。详细结果位于 `output/frozen_eval_121_20260915/remote_results/audited_summary.json`。不以该统计冒充官方 pass@1/pass@5。

本题集冻结：保存和评测仅为报告提供证据，不依据其失败结果调整模型、提示词、技能或候选代码。本次入口测试使用本地模拟服务和人工小样本，不调用真实模型或重新生成测试集。
