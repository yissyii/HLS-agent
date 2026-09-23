# 离线分类修复策略 v0.1.2

状态：独立实验模块，尚未接入 controller、context builder、现有提示词包或 RAG。不会调用模型或 Vitis；不改变修复次数、去重和停滞规则。

本模块负责规定检查步骤与修改约束。具体 API、工具版本知识、正确示例由外部知识库提供；这里不建设重复的修复案例库。

| 输入类别 | 策略 |
| --- | --- |
| compile_error | compile：编译／链接 |
| functional_or_runtime_error | functional：功能计算，按任务需要检查状态／时序；显式异常／死锁检查 |
| synthesis_error | synthesis：检查具体综合诊断，也允许先处理前端编译问题 |
| compile_or_csim_error | insufficient_evidence：证据不足 |
| 已知类别但反馈为空、仅类别或现有验证器的省略提示 | insufficient_evidence |
| repairable=false 或未知类别 | 无策略；由调用方决定处理方式 |

路由不推断细粒度根因，不以 cycle 关键词强行选择时序修复。反馈充分性检测仅识别已有兜底格式，不是语义置信度模型。泛化的“测试失败”也可能缺少有效证据，功能模板对此保持保守。

## 接口

```python
from agent.repair_strategies.router import load_pack

pack = load_pack()  # 一次加载、冻结全部模板
selection = pack.select(diagnostic)  # 现有 agent.core.contracts.Diagnostic
fragment = selection.render()       # 独立片段，不是完整请求
record = selection.snapshot()       # 策略、原因、版本、内容与哈希
```

只传验证器已经放行的 Diagnostic.feedback，不传原始日志或完整 ValidationResult。本模块不是权限检查器；选出的片段不包含输入诊断，调用方负责组合题目、源码、获准诊断和参考资料，并检查总上下文预算。模板磁盘修改不影响已加载 pack；实验中保存 pack.snapshot()，修改策略后提升 router.VERSION。

## 离线预览

从仓库根目录运行：

```powershell
python -B -m agent.repair_strategies --category compile_error --feedback "error: use of undeclared identifier 'acc'" --json
python -B -m agent.repair_strategies --category functional_or_runtime_error --feedback "Mismatch at cycle 4: expected 7, got 3"
python -B -m agent.repair_strategies --category synthesis_error --stage synthesis --feedback "ERROR: unsupported recursive call"
python -B -m agent.repair_strategies --category compile_or_csim_error --json
python -B -m unittest tools.test_repair_strategies
```

也可用 --feedback-file 指定只含已获准反馈的 UTF-8 文件。预览输出到终端，不运行主流程、不改运行产物。CLI 的 repairable 依据支持类别设置；程序接口尊重调用方的 Diagnostic.repairable。

## 后续协作与评测

v0.1.2 依次打磨编译／链接、综合、证据不足策略，新增 [三类策略审阅与验证](remaining_review.md)。路由与主流程未变；功能策略保持 v0.1.1 内容。具体工具错误的修法仍交给外部知识库，本模块只规定证据使用与检查顺序。

v0.1.1 将功能策略整理为六步：确定行为约定、使用已释放证据、沿依赖路径定位、按需检查状态时序、一致地修改、对照检查。补充中间表达式位宽、同时更新与顺序赋值、reset/enable 优先级，以及调用和测试 cycle 的区别。中文审阅案例见 [功能策略审阅](functional_review.md)，不注入模型，也不代表效果评测结果。

优先人工审阅 templates/functional.txt 的状态／时序检查顺序，再用真实但获准释放的失败反馈检查片段是否合理。验证策略效果需固定首稿、反馈、模型、工具、RAG、修复次数和预算，与原统一 repair.txt 做对照；单元测试只证明路由和模板加载行为，不证明通过率提升。

本版不实现重复候选后的再次尝试，不向主流程添加配置开关。将来接入时需单独处理模板快照、预算、决策记录和对照实验；避免在分类提示词实验中同时修改停止策略。
