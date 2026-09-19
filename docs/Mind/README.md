# 项目画布

更新日期：2026-09-18。以 `F:/Projects/ADMCmpt` 为 Obsidian vault 根目录，直接在文件列表中打开以下 `.canvas` 文件。

- [项目结构思维导图](project-structure.canvas)：工作区、框架入口、Agent、验证、知识、报告和本地产物的关系；节点内可点击链接查看文档。
- [Agent 与裸基线流程图](agent-flow.canvas)：两条入口、研发外层、模块作用、验证范围、修复循环和交付。节点与连线均可编辑。

文件采用 [JSON Canvas 1.0](https://jsoncanvas.org/spec/1.0/) 的节点与连线格式。画布中的双括号链接使用 vault 根目录相对路径；将画布单独移入其他 vault 时，应同时迁移文档或调整链接。

`Main.canvas` 是原有手工画布，本次未改动。当前完整流程以 `agent-flow.canvas` 和对应说明为准。

Markdown 对照：[项目结构](project_structure.md) · [Agent 流程与源码链接](agent_flow.md)。

## 新增模块流程图

- [System prompt 接入 Agent](system-prompt-flow.canvas)：模板加载、首稿／修复组装、预算检查、真实请求和修复循环；当前已实现。
- [Markdown / Mermaid 对照](flow.md)：程序职责和实际请求记录的查找方式。
- [RAG 流程](RAG.canvas)：已实现的检索与 Agent 修复连接。
- [RAG 接入 Agent](rag-agent-flow.canvas)：开关、反馈权限、发布门、检索、预算及修复循环；[Markdown 对照](../../report/design/rag_agent.md)。

按用户要求，今后完成新的代码模块时，同时提供一张概览流程图，优先说明输入、主要步骤、输出、必要分支和循环，并附上源码位置。明确区分已实现与计划功能；新增独立模块使用单独画布，并同步受影响的总体图，避免把所有实现细节堆在一张图中。

- [RAG 固定首稿对照实验](rag-compare-flow.canvas)：独立首稿、有效性检查、统一修复条件和配对报告；[复盘与实现](../../report/design/rag_repair_v2.md)。

- [结构化功能诊断流程](functional-diagnostics-flow.canvas)：日志事实、反馈权限、事件预算和修复／检索用途。
