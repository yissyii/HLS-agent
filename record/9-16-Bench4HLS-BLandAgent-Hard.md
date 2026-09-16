# Bench4HLS baseline vs agent 评测记录（2026-09-16）

## 摘要

在 Bench4HLS 数据集的 50 题样本上，对比「纯 baseline 一次生成」与「agent 带修复回路」两条入口的 HLS 代码生成通过率。结论：agent 把端到端通过率从 **6% 提升到 14%**（翻倍以上），主要收益在「编译」环节（30% → 82%）；但在少数 kernel 题上 agent 反而劣于 baseline（修复回路可能修坏或初始候选更差）。

## 评测配置

| 项 | 值 |
|---|---|
| 数据集 | Bench4HLS（zfsadik/Bench4HLS @ 7fac5b3） |
| 样本 | 50 题（20 kernel + 30 sequential） |
| 模型 | `qwen38`（外部 vLLM 端点，ngrok） |
| HLS 工具 | Vitis HLS 2025.2，part `xczu3eg-sbva484-1-e` |
| 验证深度 | 生成 + csim 功能验证 + 综合（csynth） |
| 并行度 | 4 workers |
| baseline 入口 | `serve/baseline_entry.py`（生成）+ `evaluation.single_task --source`（验证） |
| agent 入口 | `agent.interface.entry --task-manifest`（生成 + 验证 + 最多 2 轮修复） |
| 总耗时 | 1273s（约 21 分钟，100 次评测） |

## 题目选择

- 分类规则：Prob151–170 为 kernel（DSP/数值/加密：FIR、FFT、矩阵乘、卷积、IDCT、Cholesky、PID、CRC、SHA-1、排序等）；Prob001–150 按关键词分 sequential（FSM、序列检测、串行协议、LFSR、分支预测器、元胞自动机等）与 combinational（门电路/MUX/加减器等）。
- 剔除 61 题「过于简单」的题目（combinational 全部 + 无循环无数组的 trivial sequential）。
- 抽样 50 题：**kernel 20 题（全保留）+ sequential 30 题（按复杂度取前 30）**；combinational 0 题。

## 总体结果

| 指标 | baseline | agent | 增量 |
|---|---:|---:|---:|
| 编译通过 compile | 15 / 50 · **30%** | 41 / 50 · **82%** | **+52pp** |
| csim 功能通过 run | 4 / 50 · **8%** | 11 / 50 · **22%** | **+14pp** |
| 综合通过 synthesize | 3 / 50 · **6%** | 7 / 50 · **14%** | **+8pp** |
| **端到端通过 overall** | 3 / 50 · **6%** | 7 / 50 · **14%** | **+8pp（翻倍+）** |
| 平均 API 请求数 | 1.0 | 2.0 | +1.0（修复开销） |
| 平均单题耗时 | 12.9s | 60.3s | +47.4s |

## 分大类结果（overall 通过率）

| 大类 | 题数 | baseline | agent | 变化 |
|---|---:|---:|---:|---|
| kernel（DSP/数值/加密） | 20 | 3 · 15% | 3 · 15% | 持平 |
| sequential（FSM/协议/控制） | 30 | 0 · 0% | 4 · 13.3% | **+13.3pp** |

## 通过题目明细

- baseline 通过（3）：`Prob152`、`Prob160`、`Prob162`（均为 kernel）
- agent 通过（7）：`Prob116`、`Prob125`、`Prob137`、`Prob146`（sequential）、`Prob158`、`Prob160`、`Prob167`（kernel）
- 仅 agent 通过：`Prob116`、`Prob125`、`Prob137`、`Prob146`、`Prob158`、`Prob167`
- 仅 baseline 通过：`Prob152`、`Prob162`（agent 在这两题上反而失败）

## 关键结论

1. **agent 修复回路的主要价值在「编译」环节**（82% vs 30%）：baseline 一次性生成的代码大量直接编不过，agent 靠最多 2 轮修复把 26 道题的代码修到可编译。
2. **端到端通过率从 6% → 14%（翻倍以上）**，但绝对数仍低（7/50）：多数失败停在 `run: failed`（功能不对），说明这类硬核 HLS 题对当前模型仍是硬骨头。
3. **agent 并非严格占优**：`Prob152`、`Prob162` 两个 kernel，baseline 一次生成即通过，agent 反而没过（初始候选不同 + 修复未找回）。修复回路在少数题上会「修坏」或初始更差。
4. **收益分布**：agent 的全部净增益在 sequential 类（0→4），kernel 类打平（3→3），说明修复回路对「控制流/状态机类」的编译修复更有效。

## 环境与数据产物

- 汇总数据：`output/compare_20260916T114512599993Z/summary.json`
- 运行日志：`output/compare_run.log`
- 题目清单与分类：`data/processed/bench4hls/selection.json`
- 评测脚本：`tools/run_bench4hls_compare.py`、`tools/select_bench4hls_tasks.py`、`tools/ingest_bench4hls.py`

## 附录：评测过程中遇到的安全软件拦截问题

- 现象：评测期间多次出现 Windows 安全中心 / 火绒的拦截提示，一度怀疑 `vitis_hls.exe` / csim 生成的可执行文件被杀软拦截。
- 处理：在 Defender 中把 `F:\Projects`（覆盖 `output/` 下每次新编译的 `csim.exe`）加入排除项；`D:\FPGA`（Vitis 工具链目录）原本已在排除列表。
- 核查结论：全部 100 次评测日志中**无任何 `access denied` / `win32 error 5` 等被拦失败记录**，通过/失败均为正常的生成与验证结果，拦截提示未对评测结果造成影响。
