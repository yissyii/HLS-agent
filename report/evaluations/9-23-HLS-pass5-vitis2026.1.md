# HLS pass@5 当前冻结基线（Vitis/Vivado 2026.1）

> **报告引用规则：这是当前唯一可用于 baseline、agent 比较与汇总的 HLS pass@5 基线。**
> 不得引用 `9-18-HLS-pass5-ranked-fences.md` 的 2025.2 分数作为当前结果；该报告仅保留为历史实验记录。

冻结日期：2026-09-23。题目 122 道、每题 5 次、总槽位 610；477 份冻结候选可验证，133 个未形成候选的槽位按端到端失败计入分母。候选、测试台和生成配置保持冻结，评测期间 `model_requests=0`，不改写候选源码。

工具链为 AMD Vitis HLS / Vivado 2026.1，器件 `xczu3eg-sbva484-1-e`，时钟 5 ns。仅通过前一级的样本进入下一级；`csim_design -setup` 同时判定解析和编译，运行要求与可重复参考实现的 stdout/stderr 逐字节一致，综合要求 `csynth_design` 产出 XML 与 RTL Verilog 且无 `ERROR`。未使用隐藏测试；RTL 未评测。

| 级别 | 通过样本 | pass@1 | 至少一份通过的题目 | pass@5 |
|---|---:|---:|---:|---:|
| 解析 | 249 / 610 | 40.82% | 67 / 122 | 54.92% |
| 编译 | 249 / 610 | 40.82% | 67 / 122 | 54.92% |
| 运行 | 162 / 610 | 26.56% | 46 / 122 | 37.70% |
| 综合 | 155 / 610 | 25.41% | 45 / 122 | 36.89% |

最终状态：未形成候选 133、编译失败 228、运行失败 46、输出不匹配 41、综合失败 7、完整通过 155；合计 610。

可审计数据位于 [`output/baseline_pass5_ranked_fences_v1_20260923_vitis2026_1/`](../../output/baseline_pass5_ranked_fences_v1_20260923_vitis2026_1/)，其中 `summary.json` 为逐样本结果，`manifest.json` 为冻结清单，`run_passk_hls_eval.py` 为实际评测脚本，`batch.log` 为运行日志。完整工作目录和逐任务日志保留在远程 `/home/dingjy/sxt/zcomp-agent/output/baseline_pass5_ranked_fences_v1_20260923_vitis2026_1/`。

