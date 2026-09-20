# RAG 首次测评任务交接（远程 AI）

2026-09-19，分支 `feat/rag`。RAG 已接入 Agent（修复阶段、默认关闭、显式启用、全程留痕），88 项测试通过，本地完成真实 UG1399＋本地 Qwen 的完整修复链路 smoke，但生成服务为模拟 HTTP、验证器为 FakeValidator，**未调用真实 Vitis，不能据此宣称修复成功率提升**。本文档指导远程 AI 安装 embedding 模型并完成首次 RAG 测评。

## 交接时点的产物状态

| 内容 | 位置 | 是否已入库 |
| --- | --- | --- |
| 提取语料 | `rag/corpora/ug1399-2025.2-en`（约 7.5 MB） | ✅ 已入库 |
| 向量索引 | `rag/indexes/ug1399-qwen06b-en`（约 7.0 MB） | ✅ 已入库 |
| 发布注册表 | `rag/releases/ug1399-2025.2-en.json` | ✅ 已入库 |
| 便携模板 | `rag/runtime.json`（model/python 为 null） | ✅ 已入库 |
| 模型权重 | `Qwen3-Embedding-0.6B`（约 1.8 GB，11 文件） | ❌ 需远程安装 |
| 独立 Python 环境 | `hls-rag/venv`（torch 2.8.0 CPU 等） | ❌ 需远程安装 |
| 本机路径配置 | `rag/runtime.local.json` | ❌ gitignore，远程自建 |

## 你的两件事

1. 安装 Qwen3-Embedding-0.6B 模型与独立 Python 环境，建立 `rag/runtime.local.json`。
2. 完成首次 RAG 测评：固定首稿、相同反馈与预算，比较关闭／BM25／hybrid 的修复收益，并在远程真实 Vitis 验证。

## 一、准备运行环境

### 1. 独立 Python 环境 + 依赖

Windows（PowerShell，仓库根目录执行）：

```powershell
./rag/install_local.ps1 -InstallRoot 'F:/Workspace/hls-rag' -BasePython 'C:/path/to/python312/python.exe'
```

Linux／macOS 等价步骤（`install_local.ps1` 是 PowerShell，非 Windows 需手工执行）：

```bash
python3.12 -m venv /path/to/hls-rag/venv
/path/to/hls-rag/venv/bin/python -m pip install 'torch==2.8.0' --index-url https://download.pytorch.org/whl/cpu
/path/to/hls-rag/venv/bin/python -m pip install -r rag/requirements.lock.txt
```

依赖版本锁定在 `rag/requirements.lock.txt`（torch 2.8.0+cpu、transformers 4.57.6、sentence-transformers 5.7.0、numpy 2.5.3 等）。

### 2. 下载模型（固定 revision）

模型 `Qwen/Qwen3-Embedding-0.6B`，revision 固定为 `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`，约 1.8 GB。`rag/download_model.py` 跨平台，下载后逐文件校验 SHA256 并生成 `rag-model-manifest.json`（混合检索启动时核验此清单）：

```bash
/path/to/hls-rag/venv/bin/python -X utf8 -B rag/download_model.py \
  --output /path/to/hls-rag/models/Qwen3-Embedding-0.6B \
  --revision 97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
```

Windows 网络不稳时 `install_local.ps1` 会用 `--range-download` 对大权重文件断点续传。

> 模型在**运行时**也需要（hybrid/dense 对查询做动态向量编码），不只是建索引时；只有 BM25 不需要模型、索引、numpy 和 torch。默认 `rag_mode` 是 `hybrid`，因此默认启用 RAG 就需要模型。

### 3. 创建 rag/runtime.local.json（已 gitignore）

在仓库根目录新建 `rag/runtime.local.json`，三字段路径按远程实际填写：

```json
{
  "registry": "rag/releases/ug1399-2025.2-en.json",
  "python": "/path/to/hls-rag/venv/bin/python",
  "model": "/path/to/hls-rag/models/Qwen3-Embedding-0.6B"
}
```

Windows 的 `python` 路径形如 `F:/Workspace/hls-rag/venv/Scripts/python.exe`。配置优先级：显式 `--rag-runtime` → 本机 `rag/runtime.local.json` → 模板 `rag/runtime.json`；相对路径基于仓库根目录解析。模板的 model 为 null 时 hybrid 会显式失败（`rag_configuration_error`），不会静默降级。

### 4. 验证安装

```bash
# 发布完整性（主环境即可，纯 stdlib）
python -B -c "from rag.release import load_release, verify_release; verify_release(load_release('rag/releases/ug1399-2025.2-en.json')); print('OK')"

# 检索 smoke（用 RAG venv 的 python）
/path/to/hls-rag/venv/bin/python -X utf8 -B -m rag.retrieve 'Can a recursive function be synthesized?' --mode hybrid
/path/to/hls-rag/venv/bin/python -X utf8 -B -m rag.retrieve 'pragma HLS array_partition cyclic factor dim' --mode bm25
```

hybrid 应返回带来源的条目、不报错且完全离线（不联网）。模型加载每次进程冷启动约 7–10 秒，属正常。

## 二、首次测评

### 口径（来自 report/design/rag_agent.md 结尾）

> 固定首稿、相同反馈与预算，比较关闭／BM25／hybrid，并在远程 Vitis 完成真实验证。

三个条件只差在**修复阶段**的检索模式：

| 条件 | policy | 说明 |
| --- | --- | --- |
| 关闭 | `agent/config/policy.json`（默认） | `rag_enabled=false` |
| BM25 | 复制 hybrid policy，仅 `rag_mode` 改 `bm25` | 不需模型/索引 |
| hybrid | `agent/config/policy.rag-hybrid.json` | 需模型 + 索引 |

保持不变的量：同一 generation config 与 temperature、同一 `feedback_policy` 档位、同一预算（`rag_recall_k=20`、`rag_top_k=3`、`rag_max_bytes=6000`、`rag_query_max_chars=1600`、`rag_timeout_seconds=30`）。RAG 只在修复阶段生效，首次生成三者构造上完全相同；为让首稿确定，建议固定 temperature（或种子），把结论落在修复步骤增量上。

### 每任务每条件的运行命令

```bash
python -B -m agent.interface.entry <task_dir>/problem.txt <out_dir> \
  --config serve/runtime.local.json \
  --task-manifest <task_dir>/task.json \
  --policy agent/config/policy.rag-hybrid.json \
  --rag-runtime rag/runtime.local.json
```

- `<out_dir>` 每轮唯一；BM25 条件复制 hybrid policy 仅把 `rag_mode` 改为 `bm25`；关闭条件省略 `--policy` 与 `--rag-runtime`。
- `--config serve/runtime.local.json` 指向真实生成服务；`evaluation.single_task` 走真实 Vitis 验证，**不要用 FakeValidator**。

### 现有脚手架与缺口

`tools/run_bench4hls_compare.py` 目前只跑 baseline vs agent（默认 policy），`eval_agent` 未透传 `--policy`／`--rag-runtime`。首次测评需要小改其一：

- 给 `eval_agent` 透传 `--policy`／`--rag-runtime` 并把 agent 通道跑三次（off/bm25/hybrid 各一列）；或
- 直接用上面的命令逐任务跑三遍，再汇总每个 receipt。

汇总指标沿用 `_metrics` 字段：compile / run(csim) / synthesize / overall 通过率、修复成功增量、`api_requests`、`elapsed_seconds`，并附 `rag_history` 中每轮注入的 `injected_ids` 与字节数。

### 报告要求

- 对比表：关闭/BM25/hybrid × compile/csim/synth/overall，写明任务集（Bench4HLS 或 `selection.json`）、样本量、temperature、feedback_policy、真实 Vitis 版本。
- 保留每轮 `candidates/{n:03d}/retrieval.json` 与 `result.json` 的 `rag_history` 作为检索证据；不要用 FakeValidator 的结果宣称修复收益。

## 关键约束

- 超时、哈希变化、离线模型不可用均显式失败并停止该轮，不会伪装成无 RAG 成功。
- 召回但被预算丢弃的条目不算「模型已看到」；引用来源看候选目录实际注入的条目。
- 检索只由题目 + 该轮验证器已释放的 diagnostic 组成，不读原始日志或隐藏答案；反馈权限沿用 `category_only`／`compiler_diagnostics`／`public_diagnostics`。
- 手册摘录进入 `<REFERENCE_MATERIAL>` 区，不能冒充 `agent/context/skills.py` 中独立验证过的规则。
