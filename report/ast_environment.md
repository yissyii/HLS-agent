# 旁路检查环境与计时边界（已冻结）

正式 A/B/C 只认下面这一套。本机 Windows Vitis/clang 不计入成绩。

## 检查环境

| 项 | 冻结值 |
|---|---|
| 主机 | SSH `beida-server` → `192.168.2.246`，用户 `djy`，主机名 `amax` |
| Vitis | `/home/dingjy/sxt/zcomp-agent/vivado/2025.2/Vitis` |
| Vivado | `/home/dingjy/sxt/zcomp-agent/vivado/2025.2/Vivado` |
| 许可证 | `/home/dingjy/sxt/zcomp-agent/vivado/vivado_license.lic` |
| A 启动器 | `$VITIS/bin/vitis-run --mode hls --tcl …` |
| B/C 前端 | `$VITIS/lnx64/tools/clang-16` 的 libclang，flags：`-fsyntax-only -x c++ -std=c++14 -I$VITIS/include -I<task>` |
| `LD_LIBRARY_PATH` | `$VITIS/lib/lnx64.o` 与 `$VITIS/lnx64/tools/clang-16/lib`（缺 `libboost_filesystem.so.1.72.0` 时 clang 无法启动，记 UNKNOWN，不算编译失败） |
| 器件 / 时钟 | `xczu3eg-sbva484-1-e` / 5 ns |
| 语言 | 清单 `cxx_standard`，Bench4HLS 为 `c++14` |
| 头文件 | 公开任务目录 + `$VITIS/include` |
| 契约来源 | `task.json` 的 `top_function` + 公开 `tb.cpp` 中该名字的 AST 声明 |
| 模型 | 旁路不调用 |

## 计时边界

| 字段 | 起 | 止 | 计入方案 |
|---|---|---|---|
| `transfer_seconds` | 本机 scp/ssh 开始 | 远程 worker 进程启动前 | 否 |
| `prepare_seconds` | 复制输入或恢复测试台契约 | 进入 A 的 vitis-run / B/C 的 parse 之前 | 该方案 wall，单列 |
| `frontend_seconds` | `clang_parseTranslationUnit2` 开始 | 诊断收集结束 | B、C |
| `ast_rules_seconds` | 同一候选 TU 上 IR 遍历开始 | 规则结束 | 仅 C |
| `csim_seconds` | `csim_design` 的 vitis-run 启动 | 该进程退出 | 仅 A；含编译+仿真，不拆假的 setup 秒数 |
| `csynth_seconds` | `csynth_design` 的 vitis-run 启动 | 该进程退出 | 仅 A；csim 失败则为空 |
| `wall_seconds` | 该方案开始 | 该方案结束 | 各方案 |

A 与 B/C 串行，同机，不并行抢 Vitis。SSH 超时、license、缺库记环境异常，不记编译失败。
