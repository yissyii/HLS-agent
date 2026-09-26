# 独立自建测试模块 v0.2

**Bench4HLS 当前结构**见 [行为前端 v4 与模块分层](BENCH4HLS_BEHAVIORS.md)。
竞赛默认入口仍为 `bench4hls_competition.py` 的 legacy；v4 由独立实验 runner 选择，
未替换默认流程。下文主要介绍较早的独立 `generate.py` 模块，其接口限制不能直接用于描述 Bench4HLS 新路径。

当前增量：[验证清单与受限域比较](VERIFICATION_WORKFLOW.md)。compact 生成器 0.2.0 自动附带声明行为的覆盖清单；通用加载入口验证扩展来源；位语义检查员补齐小域全比较与绑定映射区分输入。旧包可读，主控制器仍未接入。效果和限制见 [离线配对报告](../../report/evaluations/verification_workflow_20260924.md)。

历史：[双助手 v0.3 说明](STRUCTURED_CHECKERS.md) 与 [16 道新题对照报告](../../report/evaluations/dual_checker_v3_20260923.md)。0.3.0 已完成真实对照；随后修正协议为 0.3.1，141 项回归通过，修正版真实效果待验证，未接主流程。

历史：[B v0.2 计算证据说明](CALCULATION_CHECKER.md) 与 [v0.2 验收报告](../../report/evaluations/dual_checker_v2_20260923.md)，该版真实验收未达标。

新增独立 [双检查助手实验模块](DUAL_CHECKERS.md)：已完成程序回归与真实模型首轮验收，但样例助手未达标，未接入主流程。详见 [验收结果与交付清单](../../report/evaluations/dual_checker_smoke_20260923.md)。

新增有界采样（显式开启）、独立规则／锚点审核及旧包兼容，详见 [v0.2 使用说明](V02.md)。下文原生成流程仍默认 strict；审核是旁路命令，尚未改变主流程或运行器判分。

状态：实验性原型，**未接入主控制器、默认策略、RAG 或比赛入口**。

输入题面和接口，独立生成规则与测试计划，再由固定渲染器生成自检 C++ 测试台。
候选实现仅在执行阶段读入。自测通过不等于官方功能正确、可综合、RTL 等价或满足性能要求。

## 支持范围

- 无状态、单函数、1～4 个按值标量参数、一个标量返回值。
- `bool`、`int8_t/uint8_t/int16_t/uint16_t/int32_t/uint32_t`。
- `ap_int<W>/ap_uint<W>`，W 为 1～32；native 后端需要用户显式提供 AMD 头文件目录。
- 只接受一个简单函数声明，以及限定的标准/AMD include、pragma once、简单 include guard。
- 不支持数组、指针、引用输出、浮点、stream、状态、reset、周期约束、任意公开依赖或复杂 C++ 头文件。
- 非支持接口显式拒绝，不静默改变接口。不要把这一版当成通用 HLS 测试台生成器。

## 原理与边界

1. 第一次模型请求从公开题面和接口提取 `contract.json`，包括规则、原文引用、合法输入域和歧义。
2. 引用必须能在公开输入中找到。歧义不为空时停止，不再请求测试计划。
3. 第二次模型请求生成 `test_plan.json`，包括整数预期值表达式、显式用例和采样方式。
4. Python 用受限 AST 解释器展开向量，固定 C++ 模板只负责调用被测函数、记录实际值和比较。
5. 运行器另行校验完整观测序列，并再次计算与冻结预期值的差异；不能只凭退出码 0 判通过。

这里的预期值表达式（oracle）仍是模型提出的假设。原文引用存在不代表推理正确；同一模型两次请求也不是两位独立专家。默认始终标记 `semantic_correctness=unverified`、`review_required=true`。

专家处理方法位于 `prompts/contract.md` 和 `prompts/plan.md`，可以单独打磨；执行、权限、预算、格式校验和冻结由代码保证。这是独立模块中的提示词工作流，不是已经安装或接入主 Agent 的 Codex Skill。

### 受限预期值表达式

支持整数、布尔常量、参数名、`+ - * & | ^ << >>`、比较、布尔操作和 `a if condition else b`。
禁止函数调用、属性、下标、导入、字符串、浮点、除法、取模、幂、推导式和 `eval`。
每个中间值绝对值小于 2^64，移位量在 0～63；负数右移按数学算术右移处理。
`and/or` 返回布尔语义，不采用 Python 的操作数返回语义。

运算不自动模拟固定位宽溢出，必须显式表达截断。例如 8 位无符号加法用 `(a+b)&255`。
输出超出返回类型范围会报错。输入的实际 C++ 参数类型和输出转换保持接口声明。

### 用例生成

- `exhaustive`：枚举整个声明输入域，超预算则失败，不擅自抽样。
- `boundary_random`：各参数的 min/min+1/-1/0/1/max-1/max 中合法值的笛卡尔积，再加固定种子随机输入。
- 显式用例优先；重复输入去重。随机次数表示抽样尝试数，不保证产生同样数量的新输入。
- 默认最多 4096 个不同输入，可显式设为 1～65536。
- `asserted_rule_ids` 是测试计划声称检查的规则，不是代码覆盖率或形式证明。
- 即使穷举，也仅穷举所声明的输入域，且依赖预期值表达式正确。

## 命令

从仓库根目录运行。每次 output 必须是不存在的新目录，绝不覆盖旧证据。

### 不调用模型的完整演示

```powershell
python -B tools/prepare_selftest_examples.py output/selftest_demo
python -B -m agent.selftest inspect output/selftest_demo/suites/add8
```

生成 7 道原创合成小题，公开材料在 `public/`，人工响应在 `recorded_responses/`，评测实现单独放在 `private/`，冻结测试包在 `suites/`。
这是人工计划回放，不是 LLM 效果评测。生成器不读取 `private/`。

Linux／有 g++ 的终端：

```bash
python3 -B tools/evaluate_selftest.py output/selftest_demo output/selftest_demo_eval --split all --allow-execution
```

本机 Windows 没有 PATH 上的 g++ 时，可以在已有 WSL 中运行：

```powershell
wsl.exe -d zcomp-amd-ubuntu --cd /mnt/c/Users/yissyii/zcomp-windows-harness -- python3 -B tools/evaluate_selftest.py output/selftest_demo output/selftest_demo_eval --split all --allow-execution
```

### 真实模型生成

```powershell
python -B -m agent.selftest generate problem.txt interface.h output/selftest_live --config serve/runtime.json --timeout 120
```

显式传入配置，最多两次请求，不自动重试。注意仓库默认配置是开发环境地址；使用自己的本地配置更合适。
模型只收到这两个文件的内容和第一步产生的规则，不会收到候选代码、测试答案、原始数据集或 Excel。

要复放独立审阅过的 JSON 响应：

```powershell
python -B -m agent.selftest generate problem.txt interface.h output/selftest_replay --response-file response.json
```

`response.json` 必须且仅包含 `contract` 和 `test_plan`。完整格式见 `prompts/`，可运行示例由 prepare 脚本生成。
不要把回放结果写成“真实模型生成成功”。

### 运行冻结测试包

```bash
python3 -B -m agent.selftest run output/selftest_live solution.cpp output/selftest_run --compiler g++ --allow-execution
```

`ap_int` 的 native 验证额外传 `--include-dir /path/to/Vitis/include`。这只是用主机编译器运行 AMD 类型，不是 Vitis 仿真或综合。

Vitis 后端复用现有 `HLSValidator`，只调用 C 仿真：

```bash
python3 -B -m agent.selftest run output/selftest_live solution.cpp output/selftest_vitis --backend vitis --config agent/selftest/config/runtime.wsl.example.json --timeout 120 --allow-execution
```

示例配置中的工具目录仅适用于对应本机 WSL 安装。其他机器必须提供自己的 runtime，含合法可用的器件和许可证配置；模块不安装工具或修改许可。

### 程序回归

```powershell
python -B tools/test_selftest.py -v
```

使用本机 HTTP 模拟服务验证真实模型客户端协议，不调用付费/远程模型。
没有 g++ 的平台跳过 native 编译测试，不能将跳过计为通过。在 WSL 中可运行该项。

## 输出与判分

生成目录：

| 文件 | 用途 |
| --- | --- |
| problem.txt / interface.h | 本轮明确输入的快照 |
| contract.json | 功能规则、输入域、原文引用、歧义 |
| test_plan.json / vectors.json | 预期值规则、采样与实际生成的向量 |
| testbench.cpp | 固定渲染器生成的 C++ 测试台 |
| suite.json | 文件哈希、提示词哈希、suite_id、覆盖声明、生成来源 |
| generation_result.json | 生成状态、请求数、时间；失败和阻塞也留记录 |
| generation_contract/、generation_plan/ | 两阶段实际提示、响应、模型元数据 |

运行目录有 `selftest_result.json`、输入副本、命令、工具日志；Vitis 另有 `validation.json`。
结构化失败包含输入、预期值、实际值、规则编号、用例编号；种子在 coverage 中。
结果最多列出前 20 个不匹配，完整实际值在日志中。

| 状态 | 含义 | run 退出码 |
| --- | --- | --- |
| selftest_passed | 所有冻结用例正常执行且匹配预期值 | 0 |
| selftest_failed | 正常执行，至少一条预期/实际不匹配 | 1 |
| testbench_build_error | native 后端测试台编译失败 | 2 |
| candidate_build_error | native 后端候选编译失败，不能计算功能检错率 | 2 |
| inconclusive | 缺编译器、工具环境失败、链接失败、超时、异常终止、观测缺失或证据损坏 | 2 |

生成成功状态是 `generated_unreviewed`，退出码 0；歧义阻塞或生成失败退出码 2。
Vitis 混合编译没有足够信息区分责任时，保守返回 `inconclusive`，保留原始工具分类。

## 如何评测真实模型效果

1. 先确定题集、模型配置、提示词版本、预算和开发／留出分组。
2. 仅给生成器 public 材料，为每题生成一次并冻结 suite；保留所有失败，不只挑成功题。
3. 使用 `evaluate_selftest.py --suite-root <已冻结测试包目录>` 对照独立保存的正确与错误实现。
4. 只有正确对照通过的题，其 mutants 才进入有效检出率分母；编译失败、超时不算功能检出。
5. 同时报生成失败率、正确实现误报率、无法判定数、有效 mutant 分母和耗时，不能仅报一个检出率。

当前 evaluate 工具要求选中题目的 suite 都已生成；缺失时失败。它不自动计算模型生成成功率，不能借此丢弃生成失败题。需单独汇总 generation_result 后一起报告。
工具输出中的 `completed_control_rate` 指正确对照完成整个测试执行的比例，不是全部生成任务的测试台可编译率。

内置 5 道 development、2 道 reserved 均为公开原创烟测样例。它们不构成严格未见留出集，也不代表比赛题分布。

## 安全、冻结与后续接入

- `--allow-execution` 明确允许编译运行候选 C++。当前只有进程超时与输入副本，**不是 OS 安全沙箱**；不可信候选必须放到另外配置的隔离环境中。
- 哈希检查防止意外串线和静默改动，不是数字签名；拥有写权限的恶意程序仍可伪造文件和输出。
- 修复候选时保持 suite 不变。发现测试判据错误，应独立审阅并发布新目录／新 suite_id，不覆盖旧测试。
- 本版不自动修复测试台、不自动修复候选、不新增主控制器动作，也不修改原有 baseline。
- 下一阶段优先扩展真实模型留出评测和功能反馈接入，再考虑状态/reset/流序列。先不要把“小题可运行”当成通用功能验证能力。

交付结果见 [交付清单与验证报告](../../report/deliveries/selftest_v0_1_20260923.md)。

Bench4HLS 的竞赛式闭环（自建测试台仅用于提交前修复，冻结后才运行官方测试台）见
[Bench4HLS 自测—提交—官方终测流程](../../docs/BENCH4HLS_SELFTEST_COMPETITION.md)。

Bench4HLS 的受限语义规则实验入口、支持范围和复现命令见
[Named semantic rules v3](BENCH4HLS_RULES.md)。该入口只用于独立评估，生成结果需要复核。
