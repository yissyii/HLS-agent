# 研发评测的必经分支

只要本目录存在，项目现有研发评测入口都会自动经过本模块。继续使用原来的命令即可，无须另选入口或开启重试参数。模块与具体数据集无关。

## 运行规则

一次命令启动的是一次评测会话，会话内可以有多轮尝试：

1. 保存运行配置快照，记录题目、清单、已声明依赖、策略和技能文件的哈希。
2. 在新目录运行完整的一轮评测。
3. 请求发生临时 HTTP 错误、连接中断、连接失败或底层网络超时：标记整轮无效，阻止本轮继续发出模型请求。
4. 等已经启动的子任务退出，再等待退避时间，从头运行本次评测。
5. 没有网络故障则接受这一轮结果，包括其中的编译失败、答案错误等正常失败；达到重跑上限则结束，没有有效轮次。

重跑前后检查输入哈希。输入发生变化时直接报错，需要开始新的评测会话。网络恢复不等于题目通过；接受的结果仍需检查原有 `checks`。

“完整一轮”的范围由最外层命令决定：

| 最外层入口 | 网络故障后重跑范围 |
|---|---|
| `run_eval.ps1`、`run_repair.ps1` / `evaluation.single_task` | 当前题目的生成、验证、修复全过程 |
| `run_agent.ps1`、`run.sh` / `agent.interface.entry` | 当前 Agent 任务全过程 |
| `run_baseline.ps1` / `serve.baseline` | 当前裸生成 |
| `run_baseline.sh` / `serve.baseline_entry` | 当前严格裸基线的一次生成 |
| `run_paired.sh` / `serve.paired_entry` | baseline 和 agent 两条分支一起重新运行 |
| `run_batch.ps1` / `evaluation.batch` | 本次命令选中的整批任务 |
| `tools/run_bench4hls_compare.py` | 本次命令选中的全部对比任务及两条分支，保留原来的 `--workers` 并行能力 |

批量、配对的子进程继承同一轮次，不能自行创建独立重跑循环。已发出的并行请求和正在运行的 HLS 任务可能仍需按原有超时完成收尾；新的轮次不会与旧轮次重叠。故障前完成的题也属于作废轮，不复用其候选。

## 配置

参数集中在 [retry.json](retry.json)，每个会话开始时读取一次：

| 参数 | 默认值 | 含义 |
|---|---|---|
| `max_restarts` | `2` | 首轮之外最多重跑两轮，即最多三轮；设为 0 仍会标记网络故障轮无效 |
| `initial_delay_seconds` | `5` | 首次重跑前等待 5 秒，之后指数增加 |
| `max_delay_seconds` | `30` | 等待时间上限 |
| `retry_http_statuses` | `[408,429,500,502,503,504]` | 触发整轮重跑的临时 HTTP 状态，可缩小列表 |
| `retry_generation_timeout` | `false` | 是否把模型工作进程超时也作为重跑原因 |

底层连接/读取超时会自动触发。模型工作进程的截止时间也可能因为推理慢或任务总预算不足而达到，不能确定是网络故障，因此最后一项默认关闭。需要把它也视为研发服务异常时可设为 `true`。鉴权失败、404、上下文超限、回复截断、许可证错误、编译/功能/综合失败和 HLS 工具超时均不自动重跑。

模型输出上限等参数仍在 `serve/runtime*.json`；重跑参数单独管理，提交时可以整体移除。

## 使用和产物

原有命令保持不变，例如在项目根目录运行：

```powershell
.\run_eval.ps1 path/to/task.json --config serve/runtime.local.json --cpu-only
.\run_batch.ps1 path/to/tasks --config serve/runtime.local.json --cpu-only
.\run_agent.ps1 path/to/problem.txt output/my_eval --config serve/runtime.local.json
python -B -m serve.paired_entry path/to/problem.txt output/my_pair --config serve/runtime.local.json
```

显式指定的输出目录现在是会话目录，必须不存在。Agent/基线/配对的每轮原始产物位于 `attempt_NNN/result/`。旧文件型 `run_baseline.ps1` 在 `<输出文件>.evaluation/` 保存会话，接受成功轮后才将原始回复导出到指定文件。

没有显式输出目录的单题、批量和对比入口，自动在 `output/local_eval/<时间戳-随机号>/` 创建会话，每轮产物位于自己的 `artifacts/` 下。结束时控制台输出会话文件的完整路径。

```text
<会话目录>/
├── session.json          # 会话结果、唯一有效轮次、重跑次数与所有轮次调用成本
├── runtime.json          # 固定的运行配置
├── inputs.json           # 输入文件哈希
├── attempt_000/
│   ├── scope.json        # 同轮子进程共享的配置
│   ├── requests/         # 模型传输元数据，含已发出但结果未知的请求
│   ├── network_failures/ # 触发作废的结构化故障证据
│   ├── discarded.json   # 本轮被作废时才存在
│   └── result/ 或 artifacts/  # 本轮候选、日志、原始结果
└── attempt_001/...
```

统计只采用 `session.json` 的 `valid_attempt` 指向的轮次。`valid_attempt=null` 表示没有有效轮次。作废轮保留原始证据，`valid_for_metrics=false`，不能混入正常正确率统计。单轮文件即使显示部分任务通过，也必须先服从会话层的有效性判断。

`api_requests_recorded_all_attempts` 和 `request_outcomes_unknown_all_attempts` 包括作废轮的成本。重跑增加了本地采样和调用成本，不应直接当成比赛 pass@k。退出码继承有效轮的原入口语义；重跑耗尽返回 2。Ctrl+C 中断会话，不重新启动。

## 模块边界与后续入口

- `evaluation/lifecycle.py`：所有入口统一调用的轻量接入层。
- `local_eval/guard.py`：管理整轮上下文、输出隔离、配置与输入检查、跨进程故障记录。
- `local_eval/retry.py`：判断网络故障、作废本轮、退避并重跑。
- `serve/inference.py`、`serve/agent_model.py`：通过接入层上报结构化请求状态。

新增评测程序也必须使用统一接入层，不能另写绕开它的请求/评测循环：

```python
from evaluation.lifecycle import evaluate, output_path

def run_once(args):
    work = output_path(args.output)
    # 在 work 中运行完整的一轮；使用共享 serve 模型调用模块。
    # 启动的全部子进程必须在返回前退出，并继承当前环境变量。
    return exit_code

args = parser.parse_args()
raise SystemExit(evaluate(args, run_once, output=args.output))
```

任务级 `run()`、`generate()` 等函数是执行单轮的底层组件；程序入口负责进入会话。单元测试和不执行评测的端点诊断可直接测试这些底层组件。独立离线的历史冻结验证脚本没有模型网络请求，不新增网络重跑行为。

## 提交时移除

从提交副本中删除整个 `local_eval/` 目录即可。`evaluation/lifecycle.py` 在确认该包不存在后，恢复单轮执行和原始输出路径；内部导入/运行错误不会被静默忽略。模型、Agent、评测主逻辑不需要逐个撤销重试代码。提交时不要打包研发 `output/`。

验证命令只使用人工题、本机模拟模型服务和模拟验证器：

```powershell
python -B -m unittest local_eval.test_lifecycle -v
python -B tools/test_agent_contract.py
python -B tools/test_baseline_contract.py
```
