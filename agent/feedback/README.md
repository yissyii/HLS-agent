# 验证反馈分析

状态：已实现。

- `diagnostics.py`：从验证结果提取错误类别、首要报错、文件位置、错误指纹和允许反馈的文本。
- 区分代码错误与环境、许可证、依赖、服务故障。
- 返回结构化诊断给控制器；是否继续修复由 `core/policy.py` 判断。

实际验证执行位于项目 `evaluation/`，本目录不复制 Vitis 调用逻辑。Vitis 日志到结构化诊断条目（按 compiler/synthesis/functional/environment/license 分级）的解析在 `evaluation/diagnostics.py`；本模块消费验证器按 `feedback_policy` 放行的反馈文本，并生成用于比较的错误指纹。

功能事件由 `evaluation/functional.py` 提取，任务显式选择 `functional_diagnostics` 才释放。详见 [字段、权限和流程](../../report/design/functional_diagnostics.md)。
