# Agent 提示词 v1.0.0

标准 Agent 的首次生成与每次修复均发送 `system + user` 两条消息。system 保持一致；当轮任务、源码与允许公开的诊断放在 user 消息中。RAG 已接入修复阶段，默认关闭；参考片段加入 user 消息，system 不变。见 [接入说明](../../report/design/rag_agent.md)。

先看 [程序流程图](flow.md)，再对照下面的文件和请求记录阅读。可编辑版本：`docs/Mind/system-prompt-flow.canvas`。

## 编辑位置

| 文件 | 作用 |
| --- | --- |
| `system.txt` | 英文固定约束：Vitis HLS 2025.2、完整源码输出、接口与语义保持、可综合性、参考资料的使用边界和验证证据 |
| `initial.txt` | 首次生成的阶段指令 |
| `repair.txt` | 修复阶段的指令：依据诊断做针对性修改，返回完整替换源码 |
| `manifest.json` | 提示词包 ID、版本、工具版本 |

修改任何模板后应提升 manifest 中的版本号，并建立新的对照实验。内容哈希会独立变化，因此即使忘记提升版本号，也不会把两份不同文本记成同一个指纹。文本读取时将 CRLF 统一为 LF，避免 Git 换行转换导致 Windows／Linux 的逻辑模板指纹不同。

`agent/context/prompts.py` 在每次 Agent run 开始时加载并冻结三份模板；同一 run 的修复不会重新读取磁盘模板。加载失败或空模板会在模型调用前报错，不会静默降级为旧提示词。

## 消息结构

首次生成：

```text
system: system.txt 全文
user:   <STAGE>initial</STAGE>
        initial.txt 阶段指令
        目标器件、时钟
        <PROBLEM>题目</PROBLEM>
        顶层函数与允许公开的依赖（如有）
```

修复：

```text
system: 与首次生成相同的 system.txt 全文
user:   <STAGE>repair</STAGE>
        repair.txt 阶段指令
        目标器件、时钟、题目和公开材料
        <CURRENT_SOURCE>当前候选完整源码</CURRENT_SOURCE>
        <DIAGNOSTIC>允许反馈的诊断</DIAGNOSTIC>
        已验证且显式启用的技能（如有）
```

每次是独立 HTTP 请求，都会再次发送 system 消息。代码不会手写模型专属角色 token；服务端应使用与生成模型匹配的 chat template。仅修改客户端提示词不能替代远程模板兼容性验证。

题目、候选源码和 system 都纳入预算且不被静默截断。预算不足时先移除可选技能，再缩短诊断；必要内容仍放不下就拒绝发送。当前沿用保守 UTF-8 字节估算，另为每条消息预留 64 字节的角色／模板开销；它不是精确 tokenizer 计数或任意模板下的保证。

## 兼容性

- 标准入口 `agent.interface.entry`：首稿和修复均启用共享 system。
- 严格基线 `serve.baseline_entry`：保持原题单条 user 消息，不添加 Agent 提示词。
- 旧入口 `evaluation.single_task` 使用的 `raw_initial=True`：首次生成继续原题直传、无 system；修复阶段使用新 system 与 repair 模板。这是显式保留的兼容模式，不代表标准 Agent 的消息结构。
- `initial_source`：提供现成首稿时跳过首次模型调用；若需要修复，其请求使用 repair 模板。

标准 Agent 的首稿分布会随本次提示词升级而变化。此前的评测结果不能直接作为新版本结果。后续评价 RAG 应固定首稿、system 版本、反馈和预算，分别比较无 RAG 与有 RAG。

## 证据与回溯

每次运行根目录新增 `prompt_templates.json`，保存模板全文、版本和各文本哈希；`result.json` 记录 `prompt_templates_version` 与 `prompt_templates_sha256`。

每个实际生成候选的目录保留：

- `system_prompt.txt`：本次实际使用的 system；兼容首稿为空文件。
- `prompt.txt`：本次 user 内容。此文件仍保留原名称，不代表完整 messages。
- `request.json`：包含真实角色和内容的完整请求参数。
- `context.json`：模板来源、阶段、system／user 字节数、预算和 `messages_sha256`。
- `response.txt.meta.json`：实际传输的 messages 哈希与请求结果。

`events.jsonl` 中原有 `prompt_sha256` 继续指 user 文本，新增 `messages_sha256` 标识含角色的完整消息列表。配对运行核验 Agent 提示词包指纹；研发生命周期把提示词文件加入输入哈希检查，防止重跑时混入不同文本。

## 验证

```powershell
python -B tools/test_agent_contract.py
python -B -m unittest tools.test_baseline_contract local_eval.test_lifecycle
```

这些测试使用合成题、模拟验证器与本地 HTTP 服务，检查协议和行为，不表示真实模型代码质量提高或 Vitis 已验证通过。英文 system 中预留了如何对待参考资料的规则，但没有新增检索调用或自动读取知识库。

2026-09-18 本地实测：Agent 合约 36 项通过，严格基线与研发生命周期共 20 项通过。回环服务收到的首稿／修复请求均与保存的 `request.json` 一致，共享同一 system，且完整 messages 的记录哈希与实际发送哈希一致。尚未调用远程生成模型或 Vitis 进行质量评测。


## 后续更新

### 9.19 添加了英文要求，头文件提示与状态保持

```
Use English exclusively for all code, identifiers, and comments. Never output any non-English characters (including Chinese) in the code.
Include necessary HLS headers explicitly (e.g., #include <ap_int.h>, #include <hls_stream.h>) based on the data types used.
For state across invocations, declare variables as static or global; do not use local arrays as state.
```

