# Bench4HLS baseline vs agent 评测记录（2026-09-16，全量 170 题）

## 摘要

在 Bench4HLS 数据集**全量 170 题**上，对比「纯 baseline 一次生成」与「agent 带修复回路」两条入口。结论：agent 在**每一档指标上都领先** baseline，端到端通过率 **37.1% → 48.2%（+11.2pp，多通过 19 题）**；收益在各类题上普遍存在（combinational +16.2pp、sequential +9.8pp），唯独 kernel 类打平（15% 持平）。agent 非严格占优：11 题 baseline 一次生成即通过、agent 反而失败。

## 评测配置

| 项 | 值 |
|---|---|
| 数据集 | Bench4HLS（zfsadik/Bench4HLS @ 7fac5b3），全量 170 题 |
| 模型 | `qwen38`（外部 vLLM 端点，ngrok） |
| HLS 工具 | Vitis HLS 2025.2，part `xczu3eg-sbva484-1-e` |
| 验证深度 | 生成 + csim 功能验证 + 综合（csynth） |
| 并行度 | 4 workers |
| baseline 入口 | `serve/baseline_entry.py`（生成）+ `evaluation.single_task --source`（验证） |
| agent 入口 | `agent.interface.entry --task-manifest`（生成 + 验证 + 最多 2 轮修复） |
| 评测规模 | 170 题 × 2 方法 = 340 次评测，分两批跑完 |

## 题目覆盖（全量 170 题）

- **kernel**（DSP/数值/加密）：20 题（Prob151–170：FIR、FFT、矩阵乘、卷积、IDCT、Cholesky、PID、CRC、SHA-1、排序等）
- **sequential**（FSM/协议/控制/状态机等）：82 题
- **combinational**（门电路/MUX/加减器/编码译码等）：68 题

> 其中 61 题被判定为「过于简单」（combinational 全部 + 无循环无数组的 trivial sequential），109 题为非简单题。

## 总体结果（全量 170 题）

| 指标 | baseline | agent | 增量 |
|---|---:|---:|---:|
| 编译通过 compile | 84 / 170 · **49.4%** | 139 / 170 · **81.8%** | **+32.4pp** |
| csim 功能通过 run | 64 / 170 · **37.6%** | 86 / 170 · **50.6%** | **+13.0pp** |
| 综合通过 synthesize | 63 / 170 · **37.1%** | 82 / 170 · **48.2%** | **+11.2pp** |
| **端到端通过 overall** | 63 / 170 · **37.1%** | 82 / 170 · **48.2%** | **+11.2pp（+19 题）** |
| 平均 API 请求数 | 1.0 | 1.58 | +0.58 |
| 平均单题耗时 | 20.0s | 37.9s | +17.9s |

## 分大类结果（overall 通过率）

| 大类 | 题数 | baseline | agent | 变化 |
|---|---:|---:|---:|---|
| kernel（DSP/数值/加密） | 20 | 3 · 15.0% | 3 · 15.0% | 持平 |
| sequential（FSM/协议/控制） | 82 | 23 · 28.0% | 31 · 37.8% | **+9.8pp** |
| combinational（门电路等） | 68 | 37 · 54.4% | 48 · 70.6% | **+16.2pp** |

## 按难度分层（overall 通过率）

| 难度 | 题数 | baseline | agent | 变化 |
|---|---:|---:|---:|---|
| 过于简单（too_simple） | 61 | 33 · 54.1% | 41 · 67.2% | +13.1pp |
| 非简单 | 109 | 30 · 27.5% | 41 · 37.6% | +10.1pp |

## 通过题目明细

- baseline 端到端通过：63 题
- agent 端到端通过：82 题
- **仅 agent 通过**（30 题）：Prob003、004、009、019、020、025、042、055、057、059、069、081、085、087、088、096、097、099、101、108、113、116、119、125、126、130、137、146、158、167
- **仅 baseline 通过**（11 题，agent 在这几题反而失败）：Prob016、034、043、050、064、082、103、121、131、152、162

## 与「Hard 50 题」子集的对比

| 端到端通过 | Hard 50 题 | 全量 170 题 |
|---|---:|---:|
| baseline | 3 · 6.0% | 63 · 37.1% |
| agent | 7 · 14.0% | 82 · 48.2% |
| 增量 | +8pp | +11.2pp |

> 全量里混入的 61 道简单题把两者绝对通过率都大幅拉高（简单题 baseline 也有 54.1%），所以全量的「绝对通过率」看起来高很多；但 agent 的相对优势（增量）在不同难度的题目上都稳定存在。

## 关键结论

1. **agent 在全量数据集上全面领先**：compile +32.4pp（49.4%→81.8%）、overall +11.2pp（37.1%→48.2%），多通过 19 题。核心价值仍是「把编不过的代码修到能编译」。
2. **收益按类别分化**：combinational（+16.2pp）> sequential（+9.8pp）> kernel（0pp）。修复回路对「逻辑/控制流」的编译修复有效，但对 kernel 类（DSP/数值/加密）的**功能正确性**几乎无能为力。
3. **kernel 是当前模型的硬天花板**：两类入口都只有 3/20（15%）端到端通过，agent 零增益——kernel 题难在 csim 功能仿真数值不达标，而非编译，修复回路救不了功能错误。
4. **agent 非严格占优**：有 11 题 baseline 一次生成即通过、agent 反而失败（含 Prob152、Prob162 两个 kernel）。修复回路在少数题上会「修坏」或初始候选更差。
5. **成本可控**：agent 平均 1.58 次 API 请求（baseline 1.0），约 1.9 倍耗时，换来 +19 题端到端通过。

## 环境与数据产物

- 全量合并结果：`output/merged_all.json`（340 条）
- 两批汇总：`output/compare_20260916T114512599993Z/summary.json`（Hard 50 题）、`output/compare_20260916T123755263289Z/summary.json`（remaining 120 题）
- 题目清单与分类：`data/processed/bench4hls/selection.json`、`data/processed/bench4hls/remaining.json`
- 评测脚本：`tools/run_bench4hls_compare.py`、`tools/select_bench4hls_tasks.py`、`tools/ingest_bench4hls.py`

## 附录：评测过程中遇到的安全软件拦截问题

- 现象：评测期间多次出现 Windows 安全中心 / 火绒的拦截提示，一度怀疑 `vitis_hls.exe` / csim 生成的可执行文件被杀软拦截。
- 处理：在 Defender 中把 `F:\Projects`（覆盖 `output/` 下每次新编译的 `csim.exe`）加入排除项；`D:\FPGA`（Vitis 工具链目录）原本已在排除列表。
- 核查结论：全部 340 次评测日志中**无任何 `access denied` / `win32 error 5` 等被拦失败记录**，通过/失败均为正常的生成与验证结果，拦截提示未对评测结果造成影响。
