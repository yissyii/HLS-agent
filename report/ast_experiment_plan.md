# AST 前置检查旁路实验计划

日期：2026-09-18  
状态：环境、计时、契约来源与硬规则已冻结；R02 不硬拦截。旁路执行仍缺 `compare_*` 候选。  
不修改 baseline 协议，不读取参考实现或隐藏答案编写规则，不提交、不推送。

## 1. 任务单评估（合理性）

任务单的因果设计是合理的：先旁路、再闭环；A/B/C 分离；按 **task_id** 划分集合；环境异常单独统计；误报不计“节省失败验证时间”；UNKNOWN 不触发修复。

必须改写的前提：**正式 Vitis 不在本机 Windows 上跑。** 本机 `E:/2025.2` 可启动，但默认 clang-16 目标是 `x86_64-pc-windows-msvc`，会撞上本机 VS 18 STL（要求 Clang 20）。把本机 clang 当方案 B 会把工具链不匹配算成编译失败。官方 A/B/C 一律在远程 Linux 同机实测。

其它限制（不猜测）：

| 项 | 结论 |
|---|---|
| 历史 Bench4HLS 候选 | 本机 `output/` 无 `compare_*` / `local_eval` 产物 |
| 170 题 | 已出现在 9-16/9-17 报告中，只能作历史回放，不能宣称独立泛化 |
| 人工分析题 | 25 题划入开发集，不进留出集 |
| 三次重复 A | 远程墙钟可能数小时；默认先 1 次分类，3 次重复需显式 `--repeats 3` |
| 闭环 | 旁路未过准入不得开展 |

## 2. 冻结配置（已核实）

| 项 | 值 | 证据 |
|---|---|---|
| Git HEAD | `7319acbac3ec53cc8c06dc8711e9fc3c98219191` | `git rev-parse` |
| 数据集 | Bench4HLS `zfsadik/Bench4HLS@7fac5b3`，170 题，测试台全部已适配 | `data/processed/bench4hls/index.json` |
| 顶层函数 | `TopModule` | 各 `task.json` |
| 语言标准 | `c++14` | 清单默认 |
| 头文件 | 任务目录 + 远程 `$VITIS/include`（`ap_int.h` 等） | 测试台 `#include "ap_int.h"` |
| 远程主机 | SSH `beida-server` → `192.168.2.246`，用户 `djy`，主机名 `amax` | 首次 SSH 成功：`whoami; hostname` |
| 远程 Vitis | `/home/dingjy/sxt/zcomp-agent/vivado/2025.2/Vitis`，`vitis-run` 存在 | 远程 `ls` |
| 远程 clang | `$VITIS/lnx64/tools/clang-16/bin/clang++` 存在 | 远程 `ls`；**版本字符串待 SSH 恢复后补记** |
| 许可证 | `/home/dingjy/sxt/zcomp-agent/vivado/vivado_license.lic` | 与既有 pass@k 评测脚本相同，文件是否可读待补记 |
| 器件/时钟 | `xczu3eg-sbva484-1-e` / 5 ns | `serve/runtime.json` |
| 模型 | 旁路不调用模型 | — |
| 本机 Vitis | `E:/2025.2` release 2025.2.0，**不用于正式计时** | `sourceVersion.txt`；本地 `csim_design -setup` 曾在 35s 内成功，仅作存在性探测 |

SSH 在首次探测后出现 **ICMP 通、22 端口超时**。未确认项保持未确认，不把超时当成编译失败。

## 3. 方案定义

| 方案 | 定义 | 运行位置 |
|---|---|---|
| A | 现验证流程：有测试台则 `csim_design`，通过后再 `csynth_design` | 远程 `vitis-run` |
| B | 同一 clang-16 前端，`libclang` 一次解析，只看诊断，不跑仿真，无自定义规则 | 远程 |
| C | 与 B **同一候选翻译单元**，硬规则仅 R01、R03；R02 只记录 | 远程 |

C 不二次解析候选。接口契约只来自公开 `task.json` 的 `top_function` 与公开 `tb.cpp` 的 AST，准备阶段解析测试台，不计入规则耗时。缺头文件、工具异常、超时 → `UNKNOWN`。

详见 [ast_rules.md](ast_rules.md)、[ast_environment.md](ast_environment.md)。不根据留出集改规则。

## 4. 耗时字段起止

| 字段 | 起 | 止 |
|---|---|---|
| `prepare_seconds` | 开始复制/恢复测试台契约 | 进入编译或解析前 |
| `frontend_seconds` | `clang_parseTranslationUnit2` 开始 | 诊断收集结束 |
| `ast_rules_seconds` | 同一 TU 上 IR 遍历开始 | 三条规则结束 |
| `csim_seconds` | `vitis-run` 执行 `csim_design` 进程启动 | 该进程退出（`evaluation/hls.py` 的 `elapsed_seconds`） |
| `csynth_seconds` | `vitis-run` 执行 `csynth_design` 进程启动 | 该进程退出；csim 失败则 `null` / `not_run` |
| `wall_seconds` | 该方案开始 | 该方案结束 |
| `transfer_seconds` | 本机 scp/ssh 准备 | 远程进程启动前；**不计入 A/B/C 计算时间** |

A 的 csim 同时包含编译与仿真，当前 `validate_stage` **不能**把 setup 与 run 拆开。不把这一段拆成假的“纯编译秒数”。

环境异常（license、共享库、工具超时、启动失败）单独计数，不算有效代码拦截，也不把本机 SSH 超时算进 HLS 失败。

## 5. 数据划分

- 单位：`task_id`。同一题全部候选同组。
- 开发集：25 道人工分析题（见 `MANUAL_ANALYSIS`）。
- 留出集：其余题目的**全部**候选，不按结果挑选。
- 170 题均标记 `prior_eval_exposed`。
- 清单字段：`task_id / candidate_id / source_sha256 / split / source_path / task_config_hash / prior_result_path`。

本机收获为 0 时如实记录，不拿参考实现或手写桩冒充模型候选。远程 `zcomp-agent/output` 是否仍有 9-16/9-17 候选，待 SSH 恢复后收获。

## 6. 复现命令

```powershell
python -B tools/test_static_check.py
python -B tools/evaluate_static_shadow.py freeze --host beida-server
# 有候选后再：
# python -B tools/evaluate_static_shadow.py run --list output/static_shadow/<id>/candidate_list.json --pack output/static_shadow/<id>/pack --repeats 1
```

检查器不得改写候选或任务材料；运行前后校验 `source_sha256`。
