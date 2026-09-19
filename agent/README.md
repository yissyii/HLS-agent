# HLS 智能体

本目录负责决定：使用哪些题目上下文、何时调用模型和验证工具、如何根据报错修复，以及何时停止并交付候选代码。

**当前状态：首版智能体已实现。** 主循环位于 `core/controller.py`；`evaluation/single_task.py` 已改为兼容入口，复用相同控制器。支持题目生成、公开测试验证、有限修复、候选选择、配置冻结和基线配对。

详细方案见 [设计文档](../report/design/design.md)，入口与各程序模块的关系见 [Agent 与裸基线流程图](../report/design/agent_flow.md)。

标准 Agent 现使用版本化的英文 system prompt：首稿和修复共享固定约束，阶段指令及任务材料放在 user 消息中。编辑位置、兼容模式与记录方式见 [提示词说明](prompts/README.md)。RAG 尚未接入；严格裸基线继续原题直传。

研发评测默认经过项目级 [评测管理模块](../local_eval/README.md)：网络故障使最外层命令对应的整轮评测作废并自动重跑。原命令直接生效，适用于任意题目和数据集。显式输出目录下先查看 `session.json`，再读取 `valid_attempt/result/` 中的 Agent 产物；提交副本删除 `local_eval/` 后恢复下面描述的单轮输出布局。

## 目录结构

下列模块和策略文件均已建立，图中省略 Python 包标记：

```text
agent/
├── README.md
├── interface/        # 外部入口与输入适配
│   ├── README.md
│   └── entry.py
├── core/             # 主流程、公共类型与决策策略
│   ├── README.md
│   ├── contracts.py
│   ├── controller.py
│   └── policy.py
├── context/          # 模型上下文与技能选择
│   ├── README.md
│   ├── builder.py
│   └── skills.py     # 只读规则检索；默认关闭
├── feedback/         # 验证反馈分析
│   ├── README.md
│   └── diagnostics.py
├── candidates/       # 候选版本、去重与选择
│   ├── README.md
│   └── manager.py
├── artifacts/        # 运行记录的写入逻辑
│   ├── README.md
│   └── writer.py
└── config/           # 冻结的智能体策略配置
    ├── README.md
    └── policy.json
```

职责边界：`agent/` 做决策，`serve/` 调用模型，`evaluation/` 执行与记录验证，`skill/` 保存可复用知识。严格裸基线继续由 `serve/baseline_entry.py` 独立运行。

`core/policy.py` 实现预算与停止规则，`config/policy.json` 保存这些规则使用的参数。`artifacts/` 存放记录代码，实际日志、候选和报告写入本次输出目录；`context/skills.py` 负责检索，技能内容仍放在项目根目录的 `skill/`。

Python 入口为 `python -m agent.interface.entry`，Linux 使用 `run.sh`，Windows 使用 `run_agent.ps1`。默认读取 `serve/runtime.json`；本机配置须显式传入，防止配对运行时自动套用不同配置。旧 `run_eval.ps1` 保持自动优先本机配置的行为。

默认一次初始生成、最多两次修复，通过后立即停止。参数位于 `config/policy.json`；尚未用评测题调优，默认没有启用技能规则。

## 使用

在项目根目录运行，输出目录必须不存在。下面的 `path/to/` 表示自行准备的公开开发题路径。

```powershell
# 仅有题目：生成候选，验证标为 not_run。
.\run_agent.ps1 path/to/problem.txt output/agent_run_01 --config serve/runtime.local.json

# 附带公开测试清单：生成 → csim → csynth → 有限修复。
.\run_agent.ps1 path/to/problem.txt output/agent_run_02 --config serve/runtime.local.json --task-manifest path/to/task.json --cpu-only

# 同配置、同次运行严格裸基线和智能体；Windows 无需安装 Bash。
python -B -m serve.paired_entry path/to/problem.txt output/pair_01 --config serve/runtime.local.json --task-manifest path/to/task.json --cpu-only
```

Linux 对应 `bash run.sh ...` 和 `bash run_paired.sh ...`。`--cpu-only` 仅隐藏 HLS 子进程的 GPU，不关闭外部或本地模型服务的 GPU。

任务清单沿用原有 `id`、`top_function`、`problem_file`、`source_file`、`testbench_files`、`support_files` 等字段，并新增：

```json
{
  "id": "public_example",
  "top_function": "kernel",
  "problem_file": "problem.txt",
  "source_file": "kernel.cpp",
  "testbench_files": ["tb.cpp"],
  "support_files": ["api.h"],
  "model_visible": ["api.h"],
  "feedback_policy": "public_diagnostics"
}
```

所有依赖路径相对于清单目录，必须显式声明。默认 `model_visible=[]`，不向模型提供任何依赖文件。`feedback_policy` 三档控制验证失败后放行给模型的诊断文本：默认 `category_only` 只反馈错误类别；`compiler_diagnostics` 额外给出编译与综合的具体错误（源码行 + 脱字符 + 去重后的 note），仍扣下功能测试的 `Mismatch` 等隐藏测试反例；只有确认测试诊断允许公开时才用 `public_diagnostics` 释放原始 `diagnostic_tail` 全部内容。可省略 testbench，此时显式清单提供的顶层和依赖只用于综合，功能仍标为未验证。

## 输出与退出码

- `candidate.cpp`：选中的最终候选；失败时可能保留旧候选用于诊断。
- `result.json`：状态、配对哈希、各项检查、候选历史、请求/工具次数和停止原因。
- `events.jsonl`：请求发出、工具调用、修复决策与选中候选的事件。
- `candidates/000/` 等：每次模型请求、响应、上下文来源、源码和独立 HLS 工作目录。

模型回复允许带说明文字及多个代码块。共享提取器按结构完整性、已知顶层函数、测试台特征等选择最可能完整的一个，评分相同选最先出现者；各候选的 `extraction.json` 保存选择依据。不会拼接代码块，生成截断仍判为失败。具体规则见 [设计文档](../report/design/design.md#64-响应提取与多代码块选择)。

新 agent 入口退出 0 表示成功交付候选，不代表题目通过；查看 `validation_status`、`validation_scope` 和 `checks`。基础设施故障或没有完整候选时退出非零。旧 `run_eval.ps1` 仍要求功能测试与综合均通过才返回 0。

## 验证与边界

```powershell
python -B tools/test_agent_contract.py
python -B tools/test_baseline_contract.py
python -B tools/test_code_extraction.py
# 使用本机 Vitis 和人工小题；模型是本机模拟服务，不请求真实模型。
python -B tools/smoke_agent_hls.py --config serve/runtime.local.json
```

当前上下文预算使用 UTF-8 字节数与预留量作保守检查，明确标记 `token_count_verified=false`，尚未接入模型专用 tokenizer。规则检索代码已实现，但没有附带宣称已验证的比赛技能包。单卡 AMD/ROCm 离线容器、技能增益实验和官方 pass@k 统计仍需在对应环境单独验证。
