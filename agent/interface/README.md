# 外部入口与输入适配

状态：已实现。

- `entry.py`：处理命令行、题目输入、公开材料、冻结配置、依赖组装和退出码。
- 入口：`python -m agent.interface.entry`，供 `run.sh` 和 `run_agent.ps1` 调用。
- 将输入转换为 `core/contracts.py` 的公共类型，再启动控制器。

入口不实现修复循环。基线配对回执遵循 `serve/paired_entry.py` 的现有协议。
