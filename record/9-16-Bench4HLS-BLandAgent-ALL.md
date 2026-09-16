# Bench4HLS baseline vs agent 评测记录（2026-09-16，全量 170 题）

## 摘要

在 Bench4HLS 数据集**全量 170 题**上，对比「纯 baseline 一次生成」与「agent 独立生成 + 修复回路」两条入口。按本次原始评测口径，agent 的 compile、run、synthesize、overall 汇总指标均高于 baseline，端到端通过率 **37.1% → 48.2%（+11.2pp，多通过 19 题）**；combinational +16.2pp、sequential +9.8pp，kernel 类通过数量持平（均为 3/20）。

**两条入口不共享初始代码，不能把 +19 题全部归因于修复，也不能把 11 道「仅 baseline 通过」统一解释为 agent 把正确代码修坏。** 复核发现：agent 首轮通过 71 题，修复后通过 82 题，实际从自身失败首稿中挽回 11 题；未发现首轮端到端通过、最终却失败的案例。差异还涉及仅提供错误类别的修复反馈、提前停止、模型 API 故障、仿真启动异常，以及测试程序与题意不一致。

> 本文补充分析基于已有候选代码、请求快照、验证历史和测试程序的静态复核，未重新调用模型或运行 HLS，也未修改原始汇总数据。下文保留原始通过数量，将可确认事实与待复测事项分开说明。

## 评测配置

| 项 | 值 |
|---|---|
| 数据集 | Bench4HLS（zfsadik/Bench4HLS @ 7fac5b3），全量 170 题 |
| 模型 | `qwen38`（外部 vLLM 端点，ngrok） |
| 采样配置 | 两条入口均为 `temperature=0.7`，请求未指定 seed；各自独立生成 |
| HLS 工具 | Vitis HLS 2025.2，part `xczu3eg-sbva484-1-e` |
| 验证深度 | 生成 + csim 功能验证 + 综合（csynth） |
| 并行度 | 4 workers |
| baseline 入口 | `serve/baseline_entry.py`（生成）+ `evaluation.single_task --source`（验证） |
| agent 入口 | `agent.interface.entry --task-manifest`（生成 + 验证 + 最多 2 轮修复） |
| agent 初始提示词 | 题目之外增加 Vitis HLS 源码要求、目标器件、时钟和顶层函数；不复用 baseline 候选 |
| 修复反馈 | `feedback_policy=category_only`，模型看不到日志中的具体报错及测试差异 |
| 停止策略 | `max_repairs=2`、`stagnation_limit=2`；重复候选立即停止，故不保证用满 2 轮修复 |
| 修复规则包 | `skills_enabled=false`，本次未启用 |
| 评测规模 | 170 题 × 2 方法 = 340 次评测，分两批跑完 |

## 题目覆盖（全量 170 题）

- **kernel**（DSP/数值/加密）：20 题（Prob151–170：FIR、FFT、矩阵乘、卷积、IDCT、Cholesky、PID、CRC、SHA-1、排序等）
- **sequential**（FSM/协议/控制/状态机等）：82 题
- **combinational**（门电路/MUX/加减器/编码译码等）：68 题

> 其中 61 题被结构规则标记为 `too_simple`，包括 53 道 combinational 和 8 道 sequential；其余 109 题为非简单题。规则为：combinational 参考代码非空行数 ≤15；sequential 非空行数 ≤12 且无循环、无数组。并非全部 combinational 都属于简单题，该标签也不等同于模型求解难度。

## 总体结果（全量 170 题）

| 指标 | baseline | agent | 增量 |
|---|---:|---:|---:|
| 编译通过 compile | 84 / 170 · **49.4%** | 139 / 170 · **81.8%** | **+32.4pp** |
| csim 功能通过 run | 64 / 170 · **37.6%** | 86 / 170 · **50.6%** | **+12.9pp** |
| 综合通过 synthesize | 63 / 170 · **37.1%** | 82 / 170 · **48.2%** | **+11.2pp** |
| **端到端通过 overall** | 63 / 170 · **37.1%** | 82 / 170 · **48.2%** | **+11.2pp（+19 题）** |
| 汇总字段平均 API 请求数 | 1.0（102 条有值） | 1.58（170 条有值） | +0.58，样本口径不同 |
| 汇总字段平均耗时 | 20.0s（102 条，仅验证） | 37.9s（170 条，含生成及修复） | 不可直接比较端到端耗时 |

统计口径说明：

- 通过率分母均为 170；百分点增量由原始计数计算后四舍五入，因此 run 为 22/170 = +12.9pp。
- compile 是评测器依据 csim 日志推断的状态，并非独立编译测量；Prob043 的启动异常被误标为编译失败，详见下文。
- baseline 的 `elapsed_seconds` 来自后续验证回执，不包含前面的模型生成耗时；68 条生成失败记录也未在汇总顶层填入请求数和耗时。agent 的计时则包含生成、验证和修复。因此原先「约 1.9 倍耗时」不能作为端到端成本结论，需汇总两阶段耗时及全部失败样本后再比较。

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

> 全量包含 61 道简单题，其 baseline 通过率为 54.1%，会拉高全量绝对通过率。本次 Hard 50、简单题、非简单题三个口径下均观察到 agent 的净增益；但这是单次评测，且 Hard 50 是全量的子集，尚不能据此宣称跨重复运行的稳定优势。

## 初始生成与修复收益拆分

### 两条入口是独立生成，不是同一候选的修复前后对比

`tools/run_bench4hls_compare.py` 分别调用 baseline 和 agent 入口：baseline 将题目原文传给模型；agent 经 `agent/context/builder.py` 添加 HLS 约束后重新生成，未传入 baseline 的 `candidate.cpp`。即使模型相同，也因提示词不同、采样温度非零而可能得到不同实现。

| 阶段 | Hard 50 | remaining 120 | 全量 170 |
|---|---:|---:|---:|
| baseline 独立生成后 overall 通过 | 3 | 60 | 63 |
| agent 初始候选 overall 通过 | 5 | 66 | 71 |
| agent 最终 overall 通过 | 7 | 75 | 82 |
| agent 从自身失败首稿中挽回 | 2 | 9 | **11** |

统计依据为两批各题 `agent/result.json` 中初始候选的 `candidates[0].checks` 与最终 `checks`；通过均要求 run 和 synthesize 同时为 passed。

- **修复挽回的 11 题**：Prob021、022、023、088、099、101、104、105、116、130、146。
- agent 首轮已通过的 71 题，最终全部仍通过。
- 数量上可以写成 `82 − 63 = (71 − 63) + (82 − 71) = 8 + 11`，但前面的 +8 混合了提示词差异和采样波动，不能直接解释成提示词的因果收益。
- 「仅 agent 通过的 30 题」与「修复挽回的 11 题」是不同集合：前者比较两条独立流程，后者比较 agent 自身的首轮和最终结果。

### 未发现把已通过候选修坏后丢失成功结果

`agent/core/controller.py` 在各验证阶段全部通过时立即以 `validation_passed` 停止；结束时通过 `agent/candidates/manager.py` 选择通过阶段排名最高的历史候选，同分优先保留更早候选。

因此，本次 11 道「仅 baseline 通过」不能作为“已通过的 agent 候选被修坏”的证据。确实存在失败候选在修复中退步，例如 Prob082 的错误用例从 32/405 增至 163/405，但最终选择了原先候选 000。当前排名仅区分通过阶段，不衡量功能错误数量，也可能忽略同一阶段内的改进。

## 仅 baseline 通过的 11 题：逐题归因

下表以原始候选、验证日志及测试程序为依据，不将汇总字段中的失败类别直接等同于根因。

| 题目 | agent 实际失败原因 | 修复轨迹与停止原因 |
|---|---|---|
| Prob016 | 4 位加法器使用 `a(0)`、`b(0)` 等不支持的单参数调用；日志报 `no matching function for call to object of type 'ap_uint<4>'` | 一次修复返回相同代码，`repeated_candidate`；baseline 使用 `sum = x + y` 通过 |
| Prob034 | 首轮模型 API 返回 **HTTP 503**，尚未生成候选、未进入验证 | `api_http_error`，日志明确 `no retry`；不能归为代码能力失败 |
| Prob043 | 日志已显示 `Generating csim.exe`，随后启动失败：`couldn't execute ".\\csim.exe": invalid argument`；被误标为 `compile_error` | 一次修复返回相同代码，`repeated_candidate`，未重跑同一候选；两边核心逻辑均为当前输入异或上一拍输入 |
| Prob050 | 调用不存在的 `all_ones()`、`any_ones()`、`count_ones()` 成员 | 一次修复返回相同代码，`repeated_candidate` |
| Prob064 | `(cnt == 9) ? 0 : cnt + 1` 的分支类型在 `int` 与 `ap_int<33>` 间产生转换歧义 | 一次修复返回相同代码，`repeated_candidate` |
| Prob082 | 移位寄存器 `q_reg` 为每次调用初始化为 0 的局部变量，未保持跨调用状态 | 初稿 32/405 错误，修复稿 163/405 错误，`stagnation`；最终保留候选 000 |
| Prob103 | Moore 组合逻辑在 reset 时直接把 out 设为 1；测试要求 out 仍由当前 state 决定，reset 仅约束 next_state | 修复后仍功能失败，`stagnation` |
| Prob121 | 题目允许 don't-care 任取值，测试却固定要求 0；初稿在输入 `1101` 输出 1 | 两轮候选均被判功能失败，`stagnation`；该处是测试与题意不一致，不能直接认定功能逻辑错误 |
| Prob131 | agent 在更新状态前计算 z，测试在更新状态后比较 z，输出出现一拍错位 | 修复后仍功能失败，`stagnation`；调用所对应的时序边界需明确 |
| Prob152 | 初轮遭遇与 Prob043 相同的仿真启动异常；候选代码另有 FIR 状态未正确跨调用保存的问题，后续两轮数值测试失败 | 初稿被误标编译失败 → 功能失败 → 功能失败，`repair_budget_exhausted`；最终选择候选 001 |
| Prob162 | Cholesky 功能测试通过，但额外 pragma 中 `offset=none` 不合法，综合失败；日志给出合法值 `off / slave / direct` | 两轮均 csim 通过、综合失败，非法参数未被修正，`stagnation` |

从最终失败位置看：3 题为已确认的源码编译错误（016、050、064），1 题为仿真启动异常（043），1 题为 API 故障（034），5 题被测试判功能失败（082、103、121、131、152，其中 121 存在测试口径问题），1 题综合失败（162）。Prob152 还同时存在初轮启动异常，故根因不宜强制划成互斥类别。

### 重点案例一：Prob121 的 don't-care 被测试错误收紧

题目卡诺图中 `0100`、`1001`、`1101` 为 don't-care，明确允许任选输出。测试程序 `data/processed/bench4hls/Prob121/tb.cpp` 却将三者均设为 0，并无条件比较实际值与参考值。

对 agent 初始候选的布尔表达式枚举全部 16 个输入后，所有确定的 0/1 格子均满足题意，唯一测试差异是 `1101`：agent 输出 1，测试要求 0。baseline 恰好在该格输出 0，于是通过。**该功能失败不能证明 agent 违反题意。** 但 agent 未执行到综合，且代码另有接口 pragma，不能未经验证直接补算为 overall 通过。

### 重点案例二：Prob162 功能正确，失败来自额外 pragma

初稿和修复稿的 csim 均通过，实际综合报错对应：

```cpp
#pragma HLS INTERFACE m_axi port=matrixA offset=none bundle=gmem0
```

日志明确报告 `Offset` 只接受 `off / slave / direct`。这是工具指令参数问题，并非 Cholesky 数值计算失败。模型未获得具体诊断，两次均保留错误参数。该案例不支持“kernel 题全都卡在数学功能正确性”的归因。

## 修复回路为什么没能挽回这些失败

### 详细诊断被过滤，模型只能按类别猜测

`tools/ingest_bench4hls.py` 将任务设为 `feedback_policy=category_only`；`evaluation/validator.py` 虽保存完整验证结果，却只将以下形式的文本交给模型：

```text
csim: compile_error. Detailed diagnostics are not released by this task.
csim: functional_or_runtime_error. Detailed diagnostics are not released by this task.
synthesis: synthesis_error. Detailed diagnostics are not released by this task.
```

例如 Prob016 的单参数位访问错误、Prob050 的不存在成员、Prob064 的类型歧义，对模型都只有同一句「编译失败」。Prob162 日志中的非法参数提示也没有进入修复上下文。这里需要区分“日志留存了报错”和“模型实际收到报错”，本次后者并未成立。

### 粗粒度反馈与停滞判断叠加，提前终止修复

诊断指纹由阶段、类别和反馈文本计算。`category_only` 下，同一阶段的同类失败几乎产生相同指纹；只要候选的通过阶段排名没有提高，第二次同类失败就达到 `stagnation_limit=2`。这可能把实际错误变化或部分改进也判断为停滞。

在这 11 题中，4 题以 `repeated_candidate` 停止，5 题以 `stagnation` 停止，都只发起一次修复请求；Prob152 用满两轮修复；Prob034 首轮 API 故障即终止。因而单纯增加 `max_repairs` 不足以解决问题，还要改善反馈与停止判断。

### 环境异常误归因后，模型修改代码也无法解决启动问题

`evaluation/hls.py` 的编译错误匹配规则会命中 `Simulation failed with unknown error:` 中的 `error:`，使 Prob043、Prob152 初轮的可执行程序启动异常被标为 `compile_error`。随后系统对代码进行修复，而不是重试同一候选的验证。Prob043 的修复结果恰好相同，又被候选去重逻辑停止，未获得再次验证机会。

这类记录应单列为运行环境或工具启动失败，并进一步调查；现有日志不能确认其是否由安全软件造成。

## 关键结论

1. **本次原始汇总中，agent 的总体通过数量更高，但不是逐题严格占优**：baseline 63 题、agent 82 题，仅 agent 通过 30 题、仅 baseline 通过 11 题。通过数量与错误归因应分别报告。
2. **整体增益不能全部归于修复**：agent 首轮 71 题、最终 82 题，修复从自身首稿挽回 11 题；与 baseline 的剩余差异还混合提示词和采样影响。
3. **修复能力受到当前反馈策略限制**：模型只看到错误类别，且粗粒度指纹会触发提前停止；当前结果不足以代表提供有效诊断后的修复能力。
4. **kernel 本次净增益为零，不等于模型已到硬天花板**：两边各通过 3/20，但通过集合不同；Prob162 已通过功能测试，仅因 pragma 综合失败。不能据此断言修复对 kernel 功能错误无效。
5. **未发现已端到端通过的 agent 候选被修坏后丢失成功结果**：首轮通过的 71 题全部保留成功。失败候选可能在修复中退步，但这与 baseline 独立生成成功是两回事。
6. **评测存在需要修正的口径和基础设施问题**：包括 Prob121 的 don't-care 判定、仿真启动异常误分类、HTTP 503，以及耗时统计口径不一致。原始 63/82 计数保留，修订后结果需另行复测，不能静态补算。

## 优化建议与验证顺序

| 优先级 | 建议 | 验证方式与注意事项 |
|---|---|---|
| P0 | 将 HTTP 503、仿真程序启动失败与源码编译失败分开统计；为暂时性基础设施故障设置有限重试 | 先对 Prob034、043、152 的相关失败阶段复测；记录重试次数和耗时。启动重试复用同一候选，不额外改代码 |
| P0 | 修正 Prob121 的 don't-care 判定，并审查时序类题目的调用边界说明 | 对 don't-care 格跳过固定值比较；保留原始 benchmark 结果，修正版使用独立版本和输出目录，完整重跑后再报告通过率 |
| P1 | 向模型提供候选代码的编译、综合具体错误；功能反馈按评测规则提供可公开的反例 | 避免直接公开隐藏测试；现有 `public_diagnostics` 会释放日志内容，应先明确反馈边界，再与 `category_only` 做对照 |
| P1 | 调整停滞检测，区别相同错误与相同错误类别 | 使用更有区分度的诊断或允许的进度指标；在只有类别反馈时，不把两次同类失败直接等同于没有改进 |
| P1 | 收紧初始生成要求，避免添加题目不需要的接口和优化 pragma，明确状态持久化及输出时序 | 优先复测 016、050、064、082、103、131、152、162；保持器件、验证和模型预算一致 |
| P2 | 做共享初始候选的修复消融实验 | 同一份候选一支仅验证、一支允许修复；另用相同提示词比较单次生成、独立多次采样和修复，区分反馈收益与额外请求收益 |
| P2 | 统一成本口径并进行重复评测 | 汇总生成、验证、修复及失败请求的总耗时和请求数；重复运行并报告波动，必要时记录服务支持的 seed 配置 |

以上为后续建议，本次文档补充没有修改评测代码、测试程序、任务配置或原始结果。

## 环境与数据产物

- 全量合并结果：`output/merged_all.json`（340 条）
- 两批汇总：`output/compare_20260916T114512599993Z/summary.json`（Hard 50 题）、`output/compare_20260916T123755263289Z/summary.json`（remaining 120 题）
- 题目清单与分类：`data/processed/bench4hls/selection.json`、`data/processed/bench4hls/remaining.json`
- 评测脚本：`tools/run_bench4hls_compare.py`、`tools/select_bench4hls_tasks.py`、`tools/ingest_bench4hls.py`

复核证据定位（以下路径均相对 `zcomp/`）：

- Hard 50 根目录：`output/compare_20260916T114512599993Z/`；Prob152、Prob162 位于此批。
- remaining 120 根目录：`output/compare_20260916T123755263289Z/`；其余 9 道仅 baseline 通过题位于此批。
- 各题 baseline 源码：`<批次>/<题号>/baseline_gen/candidate.cpp`；初始请求及配置见同目录 `request.json`、`config.json`。
- 各题 agent 汇总和选择结果：`<批次>/<题号>/agent/result.json`；候选与修复轨迹：`candidates/<轮次>/candidate.cpp`、`prompt.txt`、`validation.json` 和 `events.jsonl`（后者位于 agent 根目录）。
- 具体错误：各候选 `work/csim.log`、`work/synthesis.log`；Prob034 的 HTTP 503 见 `agent/candidates/000/generation.log` 及请求元数据。
- Prob121 题意与判定：`data/processed/bench4hls/Prob121/problem.txt`、`tb.cpp`；表达式见对应 agent 候选 000。
- 反馈与停止逻辑：`evaluation/validator.py`、`evaluation/hls.py`、`agent/feedback/diagnostics.py`、`agent/core/controller.py`、`agent/core/policy.py`、`agent/candidates/manager.py`。

## 附录：评测过程中遇到的安全软件拦截问题

- 现象：评测期间多次出现 Windows 安全中心 / 火绒的拦截提示，一度怀疑 `vitis_hls.exe` / csim 生成的可执行文件被杀软拦截。
- 处理：在 Defender 中把 `F:\Projects`（覆盖 `output/` 下每次新编译的 `csim.exe`）加入排除项；`D:\FPGA`（Vitis 工具链目录）原本已在排除列表。
- 原核查记录未发现 `access denied` / `win32 error 5` 等明确拒绝访问提示，但这不足以排除其他形式的执行环境异常。
- 本次进一步核对确认：Prob043、Prob152 的 agent 候选 000 均出现 `couldn't execute ".\\csim.exe": invalid argument`，且被评测器误归为编译错误。说明至少这些验证尝试受到程序启动异常影响；具体原因需结合系统事件和同一候选复测判断。
- **修订结论**：尚无充分证据认定安全软件导致这些异常，也不能继续声称环境对结果完全没有影响。应独立统计、调查和复测，保留本次原始日志，不直接修改通过计数。
