# 审核助手加固交付：0.2.1（2026-09-23）

## 结果

已完成异常输入修复、默认全域审核与证据强度标注，仍为独立模块，未接主流程，未自动修改测试台或候选代码。

上轮冻结样本回放结果：36/36 正确判据获支持，110/110 错误判据被检出，12/12 异常或不适用场景正常返回 `inconclusive`。全部回放验收断言通过，原始证据与运行期间的被测源码哈希均未变化。

这属于已知样本上的修复回归，不是新的盲测，不代表真实比赛正确率。没有新增模型请求或远程 Vitis 调用。

## 已完成的改动

### 1. 异常规则文件不再导致崩溃

解析规则或锚点附件后，先检查顶层必须为 JSON 对象，再读取字段。`[]`、`null`、字符串、数字、布尔值等会正常返回无法判断，不再抛出 `.get()` 的未捕获异常。

被拒绝附件也保存原始字节与哈希，方便复现；没有通过捕获一切异常来掩盖程序错误。新增测试同时覆盖无效 JSON、无效 UTF-8、重复键、非法嵌套字段、锚点格式错误，以及检查中途出错的状态。

### 2. 默认覆盖当前支持的完整输入域

- `audit_suite()` 与 `audit` 命令默认主输入预算由 4096 改为 65536。
- 对当前支持的完整 1～16 位、单无符号输入规则，默认可以穷举。
- 用户显式指定较小预算时严格保留，例如 `--max-cases 4096`，不会偷偷扩预算。
- `generate` 默认预算仍为 4096；测试台生成策略、模型提示词、三条规则定义均未改动。
- 补充性质可能额外执行判据，因此总执行次数仍以 `oracle_evaluations` 为准。

收益来自增加检查覆盖，不是模型变聪明或规则扩展。上轮实测 16 位审核中位耗时约由 0.615 秒增加到 5.317 秒；该数据是上轮同机预算对照，不是本轮重新测出的性能数据。这个取舍只适合当前的小输入域，不能外推到大位宽、多参数、数组或状态序列。

### 3. 报告明确区分“检查范围”和“题意正确性”

审核报告为 `schema_version=2`，新增 `auditor_version=0.2.1`；测试包模块版本仍为 0.2.0，旧测试包可继续读取。自定义报告消费方若严格要求旧 schema，需要适配。

| 字段／取值 | 含义 |
| --- | --- |
| `evidence_scope=none` | 没有完整完成可报告范围的检查；部分反例仍可能已经记录 |
| `supplied_anchors` | 仅相对于调用者给出的锚点检查 |
| `sampled_rule` | 相对于指定规则进行了抽样检查 |
| `full_domain_rule` | 相对于指定规则检查了完整声明输入域 |
| `audit_complete` | 本轮计划检查是否正常结束，不代表题意已经核验 |
| `conclusion` | 条件性文字结论，指出抽样遗漏风险或规则语义未核验 |
| `manual_review_required=true` | 当前结果仍需要人工复核 |
| `automatic_repair_allowed=false` | 当前报告不授权自动修复 |

`evidence_scope` 不是通过标记：全域检查也可能找到冲突，需与 `status` 一起阅读。所有结果仍保留 `binding_semantics=not_independently_verified` 和 `official_correctness=not_evaluated`。

原有 `supported/conflict/inconclusive` 状态及 CLI 退出码 0/1/2 不变。退出码 0 不是“官方正确”。新增字段只是当前报告的消费约定；本轮没有把它们接入主流程形成强制门禁。

## 验证记录

| 验证项 | 结果 |
| --- | --- |
| WSL 原有 49 项 + 新增 12 项回归 | 61/61 通过，39.582 秒 |
| Windows 原生 Python 新增回归 | 12/12 通过，24.635 秒；是同一组测试的跨平台复验 |
| 冻结样本中的正确判据，新默认预算 | 36/36 获支持，无误拦 |
| 冻结样本中的错误判据，新默认预算 | 110/110 检出；旧默认曾为 104/110 |
| 原来 6 个漏检，显式设回 4096 点 | 仍为 6/6 漏检，但均标注 `sampled_rule`；说明预算保持和风险披露有效，并非抽样本身已解决 |
| 原来 12 个异常／不适用场景 | 12/12 返回 `inconclusive`；原来其中 2 个崩溃 |
| 12 个规则语义错配场景 | 判断仍与旧版一致；全部标注语义未核验、需人工复核且不授权自动修复 |
| 本轮回放完整性 | `complete=true`，`failures=[]`，源码和旧证据均未变化 |

回放共实际调用审核器 176 次：146 个计分样本 + 6 个显式预算对照 + 12 个错配场景 + 12 个异常／不适用场景。上轮 7 个等价变异未重新计分。总耗时 293.289 秒，包含旧证据前后哈希核对、源码快照和报告落盘，不宜直接与上轮总耗时比较性能。

参考标签来自上轮独立 C++ 全域计算，本轮只读取冻结结果及测试包。没有修改样本答案来迎合审核器，也没有把回归样本称作新的闭卷题。

## 仍未解决

最重要的缺口仍是“题目与规则是否匹配”。例如题目要求位反转，调用者却绑定置位计数规则，穷举也只能证明符合那条错误规则。新报告强调了风险，但没有实现自动语义匹配。

下一步建议：

1. 由队友独立审核现有三条数学规则和任务绑定，检查位宽、有无符号、合法输入域、溢出语义与题面证据。
2. 冻结实现后准备未见题目及独立参考判据，包含规则适用与不适用题；参考答案不得进入模型生成输入。
3. 用真实模型生成测试台，分开统计生成成功率、判据实际正确率、审核误拒／漏检、无法判断及成本。不要把无法判断的题从分母中悄悄删掉。
4. 完成上述验证后再考虑闭环；先输出建议、人工复核，不直接让 `supported` 触发自动改代码或放行。

## 交付清单

- 修改 `agent/selftest/audit.py`：异常校验、默认预算、证据范围和条件性结论。
- 修改 `agent/selftest/__main__.py`：CLI 审核默认预算及说明。
- 更新 `agent/selftest/V02.md`：补丁说明与兼容边界。
- 新增 `tools/test_selftest_auditor_hardening.py`：12 项加固回归，包含多个异常输入子用例。
- 新增 `tools/recheck_selftest_auditor.py`：基于旧冻结样本、使用新版真实默认参数的回放工具。
- 新增本交付报告。
- 新证据目录 `output/selftest_auditor_hardening_20260923/`：
  - `recheck.json`：逐样本结果、汇总与验收断言。
  - `audits/`：176 次审核的独立报告及附件。
  - `source/`、`source_hashes.json`：本轮源码快照。
  - `baseline_hashes.json`：旧证据全部文件的哈希，运行前后核对一致。

旧评测报告及 `output/selftest_auditor_eval_20260923/` 全部保留，没有覆盖或删除。工作区其他已有修改未处理。

## 复现

```powershell
python -B -m unittest tools.test_selftest_auditor_hardening
wsl.exe -d zcomp-amd-ubuntu --cd /mnt/c/Users/yissyii/zcomp-windows-harness -- python3 -B -m unittest tools.test_selftest tools.test_selftest_v02 tools.test_selftest_study tools.test_selftest_license tools.test_selftest_auditor_hardening
wsl.exe -d zcomp-amd-ubuntu --cd /mnt/c/Users/yissyii/zcomp-windows-harness -- python3 -B tools/recheck_selftest_auditor.py output/selftest_auditor_eval_20260923 output/selftest_auditor_hardening_repeat
```

回放必须使用新的输出目录。上轮 `evaluate_selftest_auditor.py` 的 `default` 实验列显式固定为 4096，代表旧协议而非当前 API 默认值；本轮专用回放脚本不传预算来验证新默认，避免混淆。
