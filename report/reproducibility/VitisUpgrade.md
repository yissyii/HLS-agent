# Vitis 2026.1 迁移决策与实施记录

更新日期：2026-09-20

## 决策

主工具链升级到 Vitis/Vivado 2026.1，继续使用现有 `vitis-run + Tcl` 后端；本次不引入 Vitis Python API，也不引入 XSDB 调试 Skill。

RAG 语料、索引和已完成的 2025.2 检索评测保持原版本，作为独立的历史参考资产。它们不被改写为 2026.1，也不宣称已经通过 2026.1 重新验证。

## 原因

2026.1 的 `vitis.html` Python API 能创建 HLS component 并运行 C simulation、synthesis、co-simulation、implementation 和 package，但当前公开的 `HLSComponent.run()` 主要返回成功/异常及日志输出，不能直接替代验证器已有的进程隔离、硬超时、日志文件、成功标记、XML 报告和 RTL 产物核验。

Python API 仍需通过 Vitis 自带 Python 环境或 `vitis -s` 入口启动，并由 `vitis-server` 子进程提供服务，因此不会消除工具进程依赖。为了保持 Windows/Linux 行为一致和失败可诊断，本次保留 Tcl 执行路径。

2026.1 的 XSDB Python API 确实提供 `bpadd`、`rrd`、`rwr`、`mrd`、`mwr`、`stp`、`nxt`、`backtrace` 等能力，但这些接口面向连接 hw_server/TCF/GDB 的处理器目标调试；当前任务是 HLS C 仿真和综合，不是目标板 ELF 调试，暂不接入。

## 已更新内容

- `serve/runtime.json` 和本机 `serve/runtime.local.json` 指向 2026.1 Vitis/Vivado。
- Agent prompt manifest、固定 system prompt 和 Skill 版本校验切换到 2026.1。
- 运行说明、当前环境背景和 pass@k 评测脚本的当前默认版本切换到 2026.1。
- 原有 Tcl 后端、诊断提取、超时终止、报告/RTL 产物核验保持不变。
- 2025.2 RAG corpus/index、release registry、RAG 代码中的语料完整性校验和历史测评报告保留原版本。

## 验收要求

在安装 2026.1 的机器上完成以下检查后，才把 2026.1 结果用于新的评测报告：

1. `python -B tools/test_agent_contract.py`。
2. `python -B -m unittest tools.test_baseline_contract local_eval.test_lifecycle`。
3. 用真实 2026.1 运行配置执行至少一轮 csim 和 csynth，确认日志、超时清理、综合 XML 和 Verilog 产物均正常。
4. 将新结果与 2025.2 历史结果分开记录，不覆盖旧报告。

## 本机 2026.1 smoke 记录

初次使用 `serve/runtime.local.json` 对最小 `kernel(int)` fixture 执行真实
`tools/smoke_agent_hls.py`，输出目录为
`output/agent_hls_smoke_20260920T005708551788Z`。日志确认实际启动的是
`vitis-run v2026.1` 和 `HLS Build v2026.1`，因此运行配置和后端入口已经切换到
新版本；当时本机尚不能把这次运行记为通过：

- 首轮 csim 曾被 Windows Device Guard 拦截
  `D:\AMDDesignTools\2026.1\Vitis\bin\unwrapped\win64.o\vitis-run.exe`。
- 后续重试进入 HLS Build 后，Vivado 报告没有有效 license。
- 同一日志还报告目标器件 `xczu3eg-sbva484-1-e` 不受支持，原因可能是器件未安装或没有适用 license。

这批日志只记录了当时的环境故障，不能把它误报为代码迁移失败，也不能用它替代
成功验收结果。

## Vivado Basic license 安装后的复验

用户提供的 `C:\Users\24229\Downloads\VivadoBasic.lic` 已复制到
`C:\Users\24229\AppData\Roaming\XilinxLicense\VivadoBasic.lic`。该文件包含
`Vivado_Synthesis`、`Vivado_Implementation` 和 HLS package；本机忽略配置现在同时
读取 `Xilinx.lic` 与 `VivadoBasic.lic`。

以普通受限进程运行时，2026.1 已通过 license/器件初始化并进入 `csim_design`，
但 MSYS2 的 `cat.exe` 因 `Win32 error 5` 无法创建 signal pipe。以提升权限重新运行
同一命令后，smoke 完整通过，输出目录为
`output/agent_hls_smoke_20260920T023113095887Z`：

- `vitis-run v2026.1`、`HLS Build v2026.1` 均确认。
- 第一个故意包含未定义标识符的候选被正确诊断并修复。
- 修复候选的 csim、运行测试和综合均通过，状态为 `validation_passed`。
- 日志包含 `ZCOMP_STAGE_SUCCESS_CSIM` 和 `ZCOMP_STAGE_SUCCESS_SYNTHESIS`。
- 综合 XML 为 `candidates/001/work/hls_project/solution1/syn/report/kernel_csynth.xml`，
  RTL Verilog 产物已生成；估计时钟 1.203 ns、延迟 0 周期、LUT 39、FF 0、DSP 0、BRAM 0。

因此 2026.1 的真实 csim/csynth 验收现在已经完成；后续评测仍应与 2025.2 历史结果
分开保存，不覆盖旧报告。

参考：`docs/ug1400-vitis-embedded-zh-cn-2026.1.pdf`；本机 API 参考位于 `D:\AMDDesignTools\2026.1\Vitis\cli\api_docs\build\html\vitis.html` 和相邻的 `xsdb.html`。
