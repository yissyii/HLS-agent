# 模型上下文与技能选择

状态：已实现；上下文采用明确标记的保守字节预算，技能与 RAG 检索默认关闭。

- `builder.py`：组装首次生成及修复提示词，筛选允许提供给模型的材料，计算上下文预算并记录来源。
- `prompts.py`：加载并冻结 `agent/prompts/` 中的版本化 system／首稿／修复模板；同一运行各轮共享 system。
- `retrieval.py`：仅修复阶段，从题目和验证器已释放反馈查询已发布语料；独立进程限时检索，记录召回与实际注入的证据。见 [完整流程](../../report/design/rag_agent.md)。
- `skills.py`：按错误类别和关键词检索少量技能，检查版本、来源与独立验证声明。当前未附带已验证规则。

知识内容位于项目根目录的 `skill/`，本目录保存选择与组装逻辑。组件只返回上下文，不自行调用模型、执行 Vitis 或修改技能库。

请求通过 `PromptBundle.system` 与 `PromptBundle.text` 分别传递 system 和 user 内容，`messages` 属性给出完整角色列表。预算包含两部分文本及启发式消息开销；详细协议见 [提示词说明](../prompts/README.md)。
