# UG1399 英文知识库

本目录提供本地文档处理、BM25、Qwen3-Embedding-0.6B 向量检索和 RRF 混合检索。面向 Vitis HLS 2025.2 的英文问题、C++ 标识符和日志；中文问题可以通过向量匹配英文段落。当前输出是附来源的手册摘录，不是已经通过综合验证的修复规则。

## 文件与环境

| 内容 | 默认位置 |
| --- | --- |
| 源手册 | `F:/Projects/ADMCmpt/docs/ug1399-vitis-hls-en-us-2025.2.pdf` |
| 独立 Python 3.12 环境 | `F:/Workspace/hls-rag/venv` |
| 原版模型与校验清单 | `F:/Workspace/hls-rag/models/Qwen3-Embedding-0.6B` |
| 提取语料 | `rag/corpora/ug1399-2025.2-en` |
| 向量索引 | `rag/indexes/ug1399-qwen06b-en` |
| 开发检索问题 | `rag/eval_queries.json` |
| 实测报告 | [UG1399 检索方案实测](../report/model_selection/UG1399-2025.2.md) |
| 原始核验与检索证据 | `rag/reports/` 中的 JSON |
| 后续任务 | `rag/TASKS.md` |

模型 revision 固定为 `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`，权重文件约 1.19 GB（运行环境和缓存另占空间）。使用 CPU、float32 推理、1024 维、模型自带 last-token pooling 和 L2 归一化。查询添加检索 instruction，文档不添加 instruction。依赖版本保存在 `requirements.lock.txt`。无需启动 vLLM，也无需在线 embedding API；安装过程需要联网，建库和检索本身离线运行。

原始 PDF、提取全文、模型、向量和下载缓存不提交到 Git。代码、任务清单、哈希清单与检索报告可由队友审查。中文版保留在上级 docs 中，本版英文索引不混入中文重复材料。

## 处理方式与边界

1. 从封面核验 UG1399 v2025.2，记录 PDF SHA256。按 PDF 书签目录和页面坐标划分章节，去除该版固定页眉页脚。
2. 每个章节按页保留原文，再优先沿空行分成约 1700 字符的块。保留标题路径、父章节 ID、页码、语言、版本及文本哈希。跨页章节保存为 `sections.json`，可按需展开。
3. PDF 文本不是 C++ AST。代码可能是片段，表格仍是布局文本，插图不做 OCR。大段落被拆开时有质量标记；没有把手册代码当成可直接执行的完整示例。抽查不能代表全书人工校对。
4. `records.jsonl` 是规范语料；`pages.json` 保留整页提取文本，`outline.json` 保留目录，`coverage.json` 记录各页覆盖情况。向量对应的输入为末三级标题加空白归一化正文，展示时保留原文布局。
5. 编码输入设 2048 tokens 上限；超长时报错，避免静默截断。建库按长度分批编码、逐批保存，可用相同命令恢复中断任务；已完成索引不可原位覆盖。

## BM25 与混合检索

BM25 根据查询词在条目中的出现频率、稀有程度和条目长度打分。它无需模型，擅长查找 `array_partition`、`m_axi`、函数名和错误标识符。实现保留完整 C++ 标识符，同时拆分下划线与 `::`；中文采用二元分词。纯中文和纯英文没有词面交集时，BM25 通常帮不上忙。

Qwen 向量检索负责语义改写及跨语言匹配。混合模式分别召回两路候选，用 RRF 按名次融合，避免直接相加尺度不同的 BM25 和余弦分数。默认每路召回 20 条，再按父章节与文本去重，返回最多 3 条、总计不超过 6000 UTF-8 字节。预算不足时跳过整块，不截断摘录。这是字节上限，**不是生成模型的精确 token 数**。

当前库规模较小，向量保存在 NumPy 矩阵中，用精确余弦检索即可，不需要额外数据库服务。查询模型应在同一进程中复用；每次重启 CLI 都会重新加载模型和核验文件。分数仅用于排序，没有校准“不相关”阈值；低质量候选不能视为修复依据。

## 运行

以下 PowerShell 命令在 `F:/Projects/ADMCmpt/zcomp` 执行。

```powershell
# 已安装无需重复执行。全新环境可指定自己的 Python 3.12 路径。
./rag/install_local.ps1 -BasePython 'C:/path/to/python312/python.exe'
$ragPython = 'F:/Workspace/hls-rag/venv/Scripts/python.exe'

# 新建语料目录；已存在时拒绝覆盖。
& $ragPython -X utf8 -B -m rag.ingest_pdf ../docs/ug1399-vitis-hls-en-us-2025.2.pdf rag/corpora/ug1399-2025.2-en

# 可恢复未完成索引；拒绝覆盖完成索引。
& $ragPython -X utf8 -B -m rag.build_index rag/corpora/ug1399-2025.2-en rag/indexes/ug1399-qwen06b-en

# BM25 无需加载模型；hybrid 同时使用词面与向量。
& $ragPython -X utf8 -B -m rag.retrieve 'pragma HLS array_partition cyclic factor dim' --mode bm25
& $ragPython -X utf8 -B -m rag.retrieve 'Can a recursive function be synthesized?' --mode hybrid
& $ragPython -X utf8 -B -m rag.retrieve '静态变量如何跨调用保存状态？' --mode hybrid

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
    'rag/corpora/ug1399-2025.2-en',
    'rag/indexes/ug1399-qwen06b-en',
    'F:/Workspace/hls-rag/models/Qwen3-Embedding-0.6B',
)
result = retriever.search('malloc is not synthesizable', mode='hybrid')
context = ''.join(hit['context'] for hit in result['hits'])
```

## 评估与 Agent 接入

`evaluate.py` 比较同一份语料、同一份人工问题上的 BM25、dense、hybrid。报告章节级 Hit@1/3/5，保留每个问题检索到的条目和页码。24 个问题是开发 smoke set，标签只表明章节相关；命中该章节中的某块不保证其包含完整答案。评估使用较宽的 top-5 / 20000 字节预算，不能直接代表默认 top-3 / 6000 字节运行效果。

此阶段没有改动 Agent 默认提示、反馈权限或评测流程。接入时应显式启用 RAG，并记录查询、候选 ID、索引指纹和实际上下文预算；只能使用该轮允许暴露的反馈生成查询。手册摘录应放入参考资料区域，不能冒充 `agent/context/skills.py` 中独立验证过的规则。后续须在固定首稿与反馈条件下比较无 RAG、BM25 和混合检索的实际修复收益。Vitis 验证仍在远程工具链执行。

## 来源

- [AMD UG1399 2025.2](https://docs.amd.com/r/2025.2-English/ug1399-vitis-hls)：文档使用遵循 AMD 条款，模型许可证不覆盖手册。
- [Qwen3-Embedding-0.6B 官方模型](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)：Apache-2.0，安装时保存原始模型卡及文件哈希。
