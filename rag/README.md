# UG1399 英文知识库

本目录提供本地文档处理、BM25、Qwen3-Embedding-0.6B 向量检索和 RRF 混合检索。当前活动库面向 Vitis HLS 2026.1 的英文问题、C++ 标识符和日志；中文问题可以通过向量匹配英文段落。输出是附来源的手册摘录，不是已经通过综合验证的修复规则。

## 文件与环境

| 内容 | 默认位置 |
| --- | --- |
| 源手册 | `F:/Projects/ADMCmpt/zcomp/docs/ug1399-vitis-hls-en-us-2026.1.pdf` |
| 独立 Python 3.12 环境 | `F:/Workspace/hls-rag/venv` |
| 原版模型与校验清单 | `F:/Workspace/hls-rag/models/Qwen3-Embedding-0.6B` |
| 可选重排模型与校验清单 | `F:/Workspace/hls-rag/models/Qwen3-Reranker-0.6B` |
| 提取语料 | `rag/corpora/ug1399-2026.1-en-curated` |
| 向量索引 | `rag/indexes/ug1399-qwen06b-2026.1-curated` |
| 开发检索问题 | `rag/eval_queries.json` |
| 实测报告 | [UG1399 检索方案实测](../report/model_selection/UG1399-2025.2.md) |
| 发布注册表 | `rag/releases/`；仅注册表白名单可被 Agent 检索 |
| 原始核验与检索证据 | `rag/reports/` 中的 JSON |
| 后续任务 | `rag/TASKS.md` |

模型 revision 固定为 `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`，权重文件约 1.19 GB（运行环境和缓存另占空间）。使用 CPU、float32 推理、1024 维、模型自带 last-token pooling 和 L2 归一化。查询添加检索 instruction，文档不添加 instruction。依赖版本保存在 `requirements.lock.txt`。无需启动 vLLM，也无需在线 embedding API；安装过程需要联网，建库和检索本身离线运行。

可选重排模型为 `Qwen/Qwen3-Reranker-0.6B`，revision `e61197ed45024b0ed8a2d74b80b4d909f1255473`，使用 SentenceTransformers CrossEncoder 在召回候选上打相关性分数。它不参与向量建库，只有 policy 明确设置 `rag_reranker=qwen3-reranker-0.6b` 且 runtime 提供本地路径时才加载；模型和条目都通过 manifest 校验。

仓库为私有，提取语料（`rag/corpora/`）与向量索引（`rag/indexes/`）已入库，供队友直接复现检索；原始 PDF、模型权重与下载缓存不入库，需按下方 `install_local.ps1`／`download_model.py` 在新环境自行安装。代码、任务清单、哈希清单与检索报告可由队友审查。中文版保留在上级 docs 中，本版英文索引不混入中文重复材料。

## 处理方式与边界

1. 从封面核验 UG1399 v2026.1，记录 PDF SHA256。按 PDF 书签目录和页面坐标划分章节，去除固定页眉页脚。
2. 每个章节按页保留原文，再优先沿空行分成约 1700 字符的块。保留标题路径、父章节 ID、页码、语言、版本及文本哈希。跨页章节保存为 `sections.json`，可按需展开。
3. PDF 文本不是 C++ AST。代码可能是片段，表格仍是布局文本，插图不做 OCR。大段落被拆开时有质量标记；没有把手册代码当成可直接执行的完整示例。抽查不能代表全书人工校对。
4. `records.jsonl` 是规范语料；`pages.json` 保留整页提取文本，`outline.json` 保留目录，`coverage.json` 记录各页覆盖情况。向量对应的输入为末三级标题加空白归一化正文，展示时保留原文布局。Agent 只读取 `rag/releases/` 中显式注册且哈希匹配的发布库；`rag/staging/` 不可见。
5. 编码输入设 2048 tokens 上限；超长时报错，避免静默截断。建库按长度分批编码、逐批保存，可用相同命令恢复中断任务；已完成索引不可原位覆盖。

## BM25 与混合检索

BM25 根据查询词在条目中的出现频率、稀有程度和条目长度打分。它无需模型，擅长查找 `array_partition`、`m_axi`、函数名和错误标识符。实现保留完整 C++ 标识符，同时拆分下划线与 `::`；中文采用二元分词。纯中文和纯英文没有词面交集时，BM25 通常帮不上忙。

Qwen 向量检索负责语义改写及跨语言匹配。混合模式分别召回两路候选，用 RRF 按名次融合，避免直接相加尺度不同的 BM25 和余弦分数。默认每路召回 20 条，再按父章节与文本去重，返回最多 3 条、总计不超过 6000 UTF-8 字节。预算不足时跳过整块，不截断摘录。这是字节上限，**不是生成模型的精确 token 数**。

活动库使用一个向量索引保存两类条目：`fix` 覆盖不支持构造、优化与故障排除章节，`general` 覆盖其余可用于初始和修复的章节；简介和 Design Principles 被排除。Agent 的 `rag_profile=fix_first` 会在修复阶段给 `fix` 轻量优先级，`general_only` 可用于首次生成或对照实验。候选召回后可显式启用 Qwen3-Reranker-0.6B，重排器只改变候选顺序，不改变发布、证据门和字节预算。

当前库规模较小，向量保存在 NumPy 矩阵中，用精确余弦检索即可，不需要额外数据库服务。查询模型应在同一进程中复用；每次重启 CLI 都会重新加载模型和核验文件。分数仅用于排序，没有校准“不相关”阈值；低质量候选不能视为修复依据。

## 运行

以下 PowerShell 命令在 `F:/Projects/ADMCmpt/zcomp` 执行。

```powershell
# 已安装无需重复执行。全新环境可指定自己的 Python 3.12 路径。
./rag/install_local.ps1 -BasePython 'C:/path/to/python312/python.exe'
$ragPython = 'F:/Workspace/hls-rag/venv/Scripts/python.exe'

# 新建语料目录；已存在时拒绝覆盖。
& $ragPython -X utf8 -B -m rag.ingest_pdf docs/ug1399-vitis-hls-en-us-2026.1.pdf rag/corpora/ug1399-2026.1-en-curated --version 2026.1

# 可恢复未完成索引；拒绝覆盖完成索引。
& $ragPython -X utf8 -B -m rag.build_index rag/corpora/ug1399-2026.1-en-curated rag/indexes/ug1399-qwen06b-2026.1-curated

# BM25 无需加载模型；hybrid 同时使用词面与向量。
& $ragPython -X utf8 -B -m rag.retrieve 'pragma HLS array_partition cyclic factor dim' --mode bm25
& $ragPython -X utf8 -B -m rag.retrieve 'Can a recursive function be synthesized?' --mode hybrid
& $ragPython -X utf8 -B -m rag.retrieve '静态变量如何跨调用保存状态？' --mode hybrid

# 可选：安装 Qwen3-Reranker-0.6B 后即可启用重排 policy
& $ragPython -X utf8 -B -m rag.download_reranker --output F:/Workspace/hls-rag/models/Qwen3-Reranker-0.6B

# 对返回条目的 ID 展开父章节；此操作仅供查阅，不自动塞入 Agent。
& $ragPython -X utf8 -B -m rag.retrieve --expand '<record-id>'

# 每轮评估使用新的结果路径。
& $ragPython -X utf8 -B -m rag.evaluate --output output/rag_retrieval_run2.json
& $ragPython -X utf8 -B -m rag.evaluate --top-k 3 --recall-k 20 --max-bytes 6000 --output output/rag_retrieval_budget_run2.json
& $ragPython -X utf8 -B -m rag.audit --output output/rag_audit_run2.json
& $ragPython -X utf8 -B -m unittest rag.test_retrieval -v
```

程序接口可以复用同一个实例：

```python
from rag.retrieve import Retriever

retriever = Retriever(
    'rag/corpora/ug1399-2026.1-en-curated',
    'rag/indexes/ug1399-qwen06b-2026.1-curated',
    'F:/Workspace/hls-rag/models/Qwen3-Embedding-0.6B',
)
result = retriever.search('malloc is not synthesizable', mode='hybrid')
context = ''.join(hit['context'] for hit in result['hits'])
```

## 评估与 Agent 接入

启用命令、路径配置、流程和验证范围见 [Agent RAG 接入说明](../report/design/rag_agent.md)。

`evaluate.py` 比较同一份 2026.1 语料、同一份人工问题上的 BM25、dense、hybrid。报告章节级 Hit@1/3/5，保留每个问题检索到的条目和页码。24 个问题是开发 smoke set，标签只表明章节相关；命中该章节中的某块不保证其包含完整答案。评估使用较宽的 top-5 / 20000 字节预算，不能直接代表默认 top-3 / 6000 字节运行效果。

Agent 接入默认关闭；只有 policy 中显式设置 `rag_enabled=true` 才读取注册表和模型。当前查询只由题目和该轮验证器释放的诊断组成，遵守 `category_only`／`compiler_diagnostics`／`public_diagnostics` 的反馈权限，不读取原始日志或隐藏测试材料。检索失败会显式终止该轮，不静默改成无 RAG。手册摘录进入参考资料区域，不能冒充 `agent/context/skills.py` 中独立验证过的规则。后续须在固定首稿与反馈条件下比较无 RAG、BM25 和混合检索的实际修复收益。Vitis 验证仍在远程工具链执行。

## 来源

- [AMD UG1399 2026.1](https://docs.amd.com/r/en-US/ug1399-vitis-hls)：文档使用遵循 AMD 条款，模型许可证不覆盖手册。
- [Qwen3-Embedding-0.6B 官方模型](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)：Apache-2.0，安装时保存原始模型卡及文件哈希。

Agent 的诊断门和候选筛选见 [RAG 改进记录](../report/design/rag_repair_v2.md)。开发用 `rag.retrieve` CLI 保留无筛选的基础检索默认值，Agent 则使用 policy 的 2 条／2400 字节及 `diagnostic_v1`，两者用途与默认行为不同。
