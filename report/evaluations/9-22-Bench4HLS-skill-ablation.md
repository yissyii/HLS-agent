# Bench4HLS Workflow Skill 消融（S0–S3 · RAG-off · 2026-09-22）

## 结论（先给结论）

在 Bench4HLS 冻结 170 题的 RAG-off 条件下，按 `S0`→`S3` 逐个叠加 Workflow Skill，**最终通过率单调下降**：

| 条件 | 叠加技能 | 题目数 | 最终通过率 | 较上一档 |
| --- | --- | ---: | ---: | ---: |
| S0 | （无） | 170 | **112 / 170 = 65.9%** | — |
| S1 | + problem-contract | 170 | 89 / 170 = 52.4% | **−13.5pp** |
| S2 | + functional-selftest | 170 | 55 / 170 = 32.4% | **−20.0pp** |
| S3 | + synth-guard | 93 | 18 / 93 = 19.4% | −12.9pp（未跑满） |

**但本次运行整体 `status=invalid`，上述数字不能作为可信结论直接使用。** 主要原因有两条：

1. **协议违规**：`problem-contract` 契约在 S1 与 S2 之间对 **70 / 170 题**不一致（`contract_file_sha256` 不同），且由于契约先于首稿生成并注入上下文，连带使 **44 / 170 题**的 candidate 0（首稿）在 S1 与 S2 之间也不一致。这破坏了「temperature=0 冻结首稿、只差一个技能」的配对前提。
2. **S3 未跑满**：S3 只有 93 / 170 题（77 题缺失，其中 70 题正是因上述契约不一致触发提前返回而被跳过）。

因此本报告定位为**一次无效的消融初跑记录**：记录口径、原始数字与失效机理，供后续修正 runner 后重跑。方向性信号是「首批 Skill 在当前配置下不但无收益，反而明显拖累通过率」，但必须在消除契约/首稿不确定性的干净重跑后才能下结论。

逐题归因（详见第 7、8 节）进一步显示，两个技能的主要退化不是「随机变差」，而是**可定位的误拦**：

- S1 `problem_contract_ambiguous` 24 题里 **16 题在 S0 本可通过**——契约把「时钟/状态被 Bench4HLS 剥离」「算法参数未指定」这类题判为 `needs_clarification` 而中止，属过度保守的门控。
- S2 `selftest_invalid` 37 题里 **28 题在 S0/S1 本可通过**——其中 21 题是校验器「main() 未直接调用顶层函数」的**误拒**（顶层调用被包在 helper 函数里），16 题是模型生成的自测代码本身编译不过（缺 `#include <ap_int.h>`、ap_int API 误用等）。

---

## 1. 协议与条件

- 协议：`rag-off-independent-e2e-skill-ablation-v1`（RAG-off 独立端到端 skill 消融）
- 数据集：Bench4HLS 冻结 benchmark replay，170 题（Prob001–Prob170），`dataset_source_commit = 7fac5b356b0383e9463995e2c6b13a5fee27a62d`
- 反馈档：`functional_diagnostics`
- 全条件统一：`rag_enabled=false`、旧规则 `skills_enabled=false`、`max_repairs=2`、`stagnation_limit=2`、`synth_guard_mode=observe`（不做综合门禁）

| 条件 | `problem_contract_enabled` | `functional_selftest_enabled` | `synth_guard_enabled` | 生效技能 |
| --- | --- | --- | --- | --- |
| S0 | ✗ | ✗ | ✗ | （无） |
| S1 | ✓ | ✗ | ✗ | problem-contract |
| S2 | ✓ | ✓ | ✗ | + functional-selftest |
| S3 | ✓ | ✓ | ✓ | + synth-guard（observe） |

策略文件：`agent/config/policy.skill-s{0,1,2,3}.json`，运行时冻结并记录 `policy_sha256`、快照哈希。技能入口见 [skill/README.md](../../skill/README.md)。

## 2. 运行环境

- 模型：本地 vLLM `qwen38`（`http://127.0.0.1:8001/v1`），`temperature=0.0`，`enable_thinking=false`，`context_tokens=16384`，`max_tokens=4096`，`timeout_seconds=180`。模型版本无 revision 证据，仅服务别名 `qwen38`。
- 工具链：Vitis/Vivado 2026.1，`part=xczu3eg-sbva484-1-e`，`clock_ns=5`，csim 120s / synthesis 300s / total 600s。
- 并行：`workers=4`（本次运行的 key 变更，失效机理见第 9 节）。
- 运行窗口：`2026-09-22T10:44:57Z` → `12:26:06Z`，墙钟约 6068s（≈1.7h）。
- API 请求：全程记录 **1189** 次，`request_outcomes_unknown=0`。

## 3. 总结果

| 条件 | 题数 | 初稿通过 | 最终通过 | 修复净增（recoveries） |
| --- | ---: | ---: | ---: | ---: |
| S0 | 170 | 99（58.2%） | **112（65.9%）** | +13 |
| S1 | 170 | 86（50.6%） | 89（52.4%） | +3 |
| S2 | 170 | 55（32.4%） | 55（32.4%） | 0 |
| S3 | 93 | 18（19.4%） | 18（19.4%） | 0 |

关键观察：S0（无技能）能通过有限修复循环净增 **13** 题（99→112），S1 只剩 +3，S2/S3 归零。叠加技能越多，修复循环越「修不动」，说明技能不是单纯无效，而是在**挤压/阻断修复**。

### 分级验证（最终）

| 条件 | compile | run（csim） | synthesize |
| --- | ---: | ---: | ---: |
| S0 | 165 | 113 | 112 |
| S1 | 120 | 90 | 89 |
| S2 | 64 | 55 | 55 |
| S3 | 20 | 18 | 18 |

（`run`/`synthesize` 是 `compile` 通过后的窄化子集，最终通过率 = synthesize 通过数。）

## 4. 配对差分（paired deltas）

| 对比 | 赢 | 输 | 平 | 净变化 |
| --- | ---: | ---: | ---: | --- |
| S1 − S0 | 6 | 29 | 135 | −23 |
| S2 − S1 | 2 | 36 | 132 | −34 |
| S3 − S2（93 题重叠） | 2 | 7 | 84 | −5 |
| S3 − S0（93 题重叠） | 2 | 40 | 51 | −38 |

逐档叠加时「输」远多于「赢」：S1 相比 S0 净 −23，S2 相比 S1 净 −34。S3 相对 S2 的 −5 因重叠样本只有 93 题且 S2 本身已很低，参考意义有限。

## 5. 开销

| 条件 | 平均 model 请求 | 平均 workflow 请求 | 平均总 token | 平均耗时（s） |
| --- | ---: | ---: | ---: | ---: |
| S0 | 1.494 | 0.0 | 2378.5 | 28.05 |
| S1 | 1.918 | 1.0 | 4551.5 | 38.37 |
| S2 | 2.500 | 1.741 | 6514.9 | 52.17 |
| S3 | 1.978 | 1.527 | 5055.8 | 42.40 |

叠加技能带来明显开销：S2 相比 S0 平均 token 约 ×2.7、耗时约 ×1.9。这些开销本应换得修复收益，但实际通过率反而下降。

## 6. 技能触发与失败模式

| 条件 | contract_ready | contract_failures | selftest_runs | selftest_failures | synth_guard_runs | synth_guard_findings |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| S1 | 124 | 43 | — | — | — | — |
| S2 | 126 | 40 | 107 | 41 | — | — |
| S3 | 49 | 41 | 36 | 16 | 18 | 12 |

失败类别分布（`receipt.category`）：

| 类别 | S0 | S1 | S2 | S3 |
| --- | ---: | ---: | ---: | ---: |
| `problem_contract_ambiguous` | 0 | 24 | 23 | 24 |
| `selftest_invalid` | 0 | 0 | 37 | 23 |
| `workflow_output_error` | 0 | 19 | 17 | 17 |
| `functional_or_runtime_error` | 15 | 4 | 3 | 0 |
| `generation_incomplete` | 3 | 4 | 9 | 6 |
| `context_budget_exceeded` | 2 | 9 | 5 | 0 |

即：S1 的主要新失败是 `problem_contract_ambiguous`（24）+ `workflow_output_error`（19）；S2 的主要新失败是 `selftest_invalid`（37）——自测在 107 次运行中判「无效」41 次，成为最大单项退化来源。S3 的 `synth_guard` 在 18 次触发中报 12 个发现，但仅为 observe，未直接作门禁。

## 7. S1 `problem_contract_ambiguous` 逐题归因（24 题）

`problem-contract` 在 S1 对 24 题返回 `status=needs_clarification` 并中止（`message="Problem contract requires clarification"`，`workflow_skills_used=[]`）。逐题读 `workflow/problem-contract/response.txt` 的 `unresolved`/`assumptions`/`risk_points`，归为两类。

### 7.1 时序/时钟逻辑与无状态 C++ 原型冲突（12 题）

题目描述触发器/FSM/计数器/寄存器等「时钟沿触发」行为，但冻结的 C++ 原型 `void TopModule(...)` 不含 clk 参数、也没有显式状态机制，契约模型因此判「无法建模上升沿/跨调用状态」而要求澄清。这主要是 **Bench4HLS 剥离时钟后的接口限制**，而非题目本身缺失。

| 题号 | 归因要点 |
| --- | --- |
| Prob027 | 带时钟使能的寄存器（D 触发器），原型无 clk，无法建模上升沿与 q 初值 |
| Prob033 | 上升沿捕获寄存器，原型无 clk |
| Prob037 | 计数器状态跨调用维持，原型无 clk/state |
| Prob043 | 寄存输入首拍初值未定义 |
| Prob047 | 触发器无 clk、q 初值未定义 |
| Prob051 | 触发器无 clk、状态持久化机制未定 |
| Prob052 | 边沿检测寄存器首拍初值未定义 |
| Prob058 | 带使能 DFF，无 clk/state、Q 初值未定义 |
| Prob071 | 三段 FSM，无 clk/state，且「每调用是否代表一个时钟周期」不明 |
| Prob075 | 双边沿触发器，无 clk/边沿指示 |
| Prob092 | FSM 同步/异步复位、初态、复位时序歧义 |
| Prob123 | 同步 FSM，原型无 clk |

### 7.2 算法细节/常量未定义（12 题）

题目描述复杂算法，但关键参数/方程/变体未指定，契约模型对真实欠规范要求澄清。

| 题号 | 归因要点 |
| --- | --- |
| Prob126 | 波形用符号变量（a,b,d,e），无法推导逻辑函数 |
| Prob129 | y=5,6,7 行为未定义、Y0 是 LSB 还是 MSB 歧义 |
| Prob152 | 延迟线寄存器初态、19-bit 溢出、舍入模式未定义 |
| Prob153 | 缓冲/FF 初态、定点溢出/舍入、点积用当前还是移入值歧义 |
| Prob155 | FFT 浮点容差、位反转 in-place、CORDIC/查表精度未定义 |
| Prob159 | 一阶 IIR 滤波公式、积分与输出顺序、输出 latency 未定义 |
| Prob160 | CRC-32 变体（init/xorout/refin/refout）未指定 |
| Prob163 | Montgomery 乘法 mprime 定义、m 偶/零等非法输入未定义 |
| Prob166 | SHA valid 标志在非法输入时的行为未定义 |
| Prob168 | 标准差「small minimum」常量、float_n 是否恒为 100 未定义 |
| Prob169 | ADI 前向/后向求解方程、边界与初值未定义 |
| Prob170 | FDTD 第 4 步更新 ex 还是 hz、`_fict_` 到 `ey[0][*]` 映射歧义 |

**跨条件影响**：这 24 题中 **16 / 24 在 S0 通过**（模型本可解出，被契约门误拦），S1 下 0 / 24 通过。契约「needs_clarification」门控过度保守，是 S1 相对 S0 净 −23 的主要来源之一。

## 8. S2 `selftest_invalid` 逐题归因（37 题）

`functional-selftest` 在 S2 对 37 题触发 `selftest_invalid`。读 `result.json` 的 `message` + `workflow_history`（`functional-selftest` 的 ready/attempt 状态与 `category`），归为两个子因。

### 8.1 校验器误拒——「main() 未直接调用顶层函数」（21 题）

21 题 `message="Generated self-test main() does not directly exercise the top function"`。抽查生成源码发现：模型把 `TopModule(...)` 调用放进 `check(...)`/`run_test(...)` 等 **helper 函数**（声明在 `main()` 之前），`main()` 只调用这些 helper。而 `parse_selftest` 的校验只扫描 `main() {` **之后**的源码里是否有字面 `TopModule(` 调用，漏掉 helper 里的调用，误判「未直接调用顶层函数」。**生成的自测代码本身有效、确实调用了顶层函数，这是校验启发式误拒，不是模型错误。**

| 题号（21） | 自测结构 |
| --- | --- |
| Prob016, 031, 040, 042, 045, 049, 050, 053, 056, 073, 077, 089, 091, 110, 119, 124, 134, 148, 150, 164, 167 | 顶层调用包裹在 `check()`/`run_test()` helper 内，`main()` 只调 helper |

### 8.2 自测代码本身编译失败（16 题）

16 题 `message="Generated self-test failed independently..."`，`workflow_history` 里 `functional-selftest` attempt `status=failed`。读 `candidates/000/selftest.json` 的编译错误逐条归因：

| 错误签名 | 题号 | 归因 |
| --- | --- | --- |
| `no template named 'ap_uint'` | Prob025, 063, 096, 102 | 自测缺 `#include <ap_int.h>` |
| `invalid operands ... 'const char[...]' and 'const char *'` | Prob036, 069, 097, 103 | 字符串字面量拼接错误 |
| `no member named 'str'` / `'to_uint' in 'ap_bit_ref'` | Prob021, 088 | 调用了 ap_int/ap_bit_ref 不存在的方法 |
| `ambiguous constructor` / `no matching function` / `ambiguous conversion` | Prob004, 079, 111 | ap_uint 构造/调用/强转误用 |
| 其他（缺 `;` / 重声明 / 缺头文件） | Prob035, 064, 156 | 语法错误、`class member cannot be redeclared`、缺 `<initializer_list>` |

**跨条件影响**：这 37 题中 **28 / 37 在 S0 与 S1 均通过**（不加自测时模型可解出），S2 下 0 / 37 通过。自测门控造成 28 个净回归，其中 21 个是校验器误拒（8.1，属代码缺陷，可修）、16 个是自测代码编译失败（8.2，属模型生成 ap_int 自测代码的可靠性问题）。

## 9. 协议违规与失效机理（重点）

本次运行 `summary.status = "invalid"`，`protocol_error = "Prob001: contract differs between S1 and S2"`。逐题核对后确认不是孤例：

1. **契约不一致 70 / 170**：`problem-contract` 产物 `contract.json` 的 `contract_file_sha256` 在 S1 与 S2 之间不同。抽查 Prob001 显示，两边的 `request_sha256` **完全相同**（模型收到同一请求），但内容字段 `boundaries` / `risk_points` 不同——说明这是**模型在 temperature=0 下仍存在生成不确定性**，而非上下文污染。
2. **首稿不一致 44 / 170**：`candidate0_sha256`（首稿源码哈希）在 S1 与 S2 之间不同。契约先于首稿生成并注入上下文，故契约不确定性向下游级联到首稿，进一步破坏配对前提。
3. **S3 缺 77 题**：runner 在 S2 阶段检测到契约/首稿不一致即对当前题 `return`，跳过其 S3。因此 93 题的 S3 不是「跑满后的抽样」，而是「被提前中止后的残余」，与 S0–S2 不可直接比。

**可能机理（待验证）**：`temperature=0` 依赖 vLLM 采样在单请求顺序执行下才稳定；本次新增 `--workers 4` 并行，同批多个请求在 batched inference 下可能引入浮点/批序不确定性，从而打破「冻结首稿」假设。该假设正是协议里 `_run_task` 对 candidate0 / contract 做 S1 基准比对的前提。**修正方向**：先以 `--workers 1` 顺序重跑 S0–S3 验证是否存在同源不确定性；若仍存在，需进一步固定 vLLM 采样（如 `seed`/`top_p`/禁用并行批）或接受契约/首稿在跨条件比较中不可配对。

## 10. 复现

```bash
python tools/run_skill_compare.py \
  --all --conditions S0,S1,S2,S3 \
  --workers 4 \
  --output output/remote-skill-ablation/full-5335e89-par4
```

- 配置默认 `serve/runtime.rag-eval.json`（qwen38 + temperature=0），技能目录默认 `skill/`。
- 产物根：`output/remote-skill-ablation/full-5335e89-par4/`；有效轮 `attempt_000`，结果在 `.../attempt_000/result/`（`summary.json`、`runs/{ProbXXX}/{S0..S3}/`、`policies/S{0..3}.json`、`workflow_skills/`）。
- 会话：`.../session.json`（`status=completed_with_failures`，`valid_attempt=attempt_000`，exit_code 1）。
- 逐题审计证据在 `.../runs/{ProbXXX}/{cond}/`（`result.json`、`events.jsonl`、`workflow/problem-contract/contract.json`、`workflow/functional-selftest/bundle.json` 等）。

## 11. 后续

- **先修 runner / 采样确定性**（第 9 节），再决定是否重跑；当前结果不构成「Skill 有害」的正式证据，只能作为「需要警惕」的方向性信号。
- 归因已就绪，可直接对症修：
  1. **修校验器误拒（8.1，21 题）**：放宽 `agent/workflow/runtime.py` 的 `parse_selftest` 对「main() 直接调用顶层函数」的判定——顶层调用包在 `check()`/`run_test()` 等 helper 里应视为通过；或改为在整个源文件范围校验顶层函数被调用（而非仅 `main() {` 之后）。
  2. **修自测代码编译失败（8.2，16 题）**：在 `functional-selftest` 生成 prompt 里强制约束（必含 `#include <ap_int.h>`、禁用字符串字面量 `+` 拼接、只用 `ap_int::to_uint/to_string` 合法方法、给出位选 `operator[]` 而非函数调用），或对自测代码加一次编译修复回环。
  3. **放宽契约过度保守（7.1/7.2）**：对「时钟/状态被 Bench4HLS 剥离」的题（7.1，12 题）在契约模型里显式告知「原型无 clk 属已知接口约定，按每调用一拍的语义建模」；对「算法参数未指定」的题（7.2，12 题）允许契约以显式 `assumptions` 落盘并继续，而非 `needs_clarification` 直接中止。
  4. **修契约/首稿不确定（第 9 节）**：先 `--workers 1` 顺序重跑验证是否源于并行 batched inference；若仍复现则固定 vLLM 采样或调整协议对比基准。
- 干净重跑后若仍复现「叠加技能单调降低通过率」，再判断是 prompt/验收条件问题还是技能设计本身与当前修复循环冲突。
- S3 的 `synth-guard` 目前 observe-only 且样本不足，暂无法评估其独立贡献。
