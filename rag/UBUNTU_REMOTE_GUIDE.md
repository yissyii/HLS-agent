# 远程 Ubuntu/5090D 运行指南

本文用于在远程 Ubuntu 主机上运行当前 `feat/rag` 分支的 Vitis HLS 2026.1 RAG 和 Bench4HLS 实验。假设远程主机负责 Vitis 验证，并通过本机或局域网 HTTP 服务提供生成模型；Embedding 和可选 Reranker 直接从本地文件加载，不需要放进 vLLM 容器，也不需要在线 Embedding API。

## 1. 获取代码并确认版本

建议把仓库放在没有空格的目录，例如 `/opt/hls-agent` 或 `$HOME/HLS-agent`：

~~~bash
git clone --branch feat/rag https://github.com/yissyii/HLS-agent.git "$HOME/HLS-agent"
cd "$HOME/HLS-agent"
git fetch origin feat/rag
git switch feat/rag
git pull --ff-only origin feat/rag
git status --short
git log -1 --oneline
~~~

应看到提交 `829655f feat(rag): 升级 UG1399 2026.1 并接入分类检索与可选重排` 或其后续提交。仓库中已经包含以下活动产物，不要重新提取 PDF 或重建向量索引：

~~~text
docs/ug1399-vitis-hls-en-us-2026.1.pdf
rag/corpora/ug1399-2026.1-en-curated
rag/indexes/ug1399-qwen06b-2026.1-curated
rag/releases/ug1399-2026.1-en-curated.json
~~~

## 2. 检查 Ubuntu、GPU、Vitis 和生成服务

~~~bash
cat /etc/os-release | head
python3 --version
nvidia-smi
command -v vitis_hls || true
~~~

加载 Vitis HLS 2026.1 的环境脚本。路径按远程安装位置修改：

~~~bash
source /tools/Xilinx/Vitis/2026.1/settings64.sh
source /tools/Xilinx/Vivado/2026.1/settings64.sh
vitis_hls -version
~~~

如果远程生成模型由 vLLM 提供，先确认 OpenAI 兼容接口可访问；下面的地址只是示例：

~~~bash
curl -fsS http://127.0.0.1:8000/v1/models
~~~

RAG 的本地模型与生成服务是两条独立链路：

~~~text
Agent ──HTTP──> 生成模型/vLLM
  │
  └──本地 Python worker──> BM25 + Qwen Embedding + 可选 Reranker
~~~

当前仓库中的 Embedding 和 Reranker 类固定使用 CPU，以保持跨环境结果稳定；即使机器有 5090D，RAG 查询仍会在 CPU 上执行。RAG 每轮只编码一条查询并重排少量候选，通常可以接受。不要因为看到 5090D 就把 Embedding 服务强行放进 Vitis 或 vLLM 容器。

## 3. 创建独立 Python 环境

推荐 Python 3.12，并把环境和模型放在工作盘，例如 `/data/hls-rag`：

~~~bash
export REPO="$HOME/HLS-agent"
export RAG_HOME="/data/hls-rag"
export RAG_VENV="$RAG_HOME/venv"
mkdir -p "$RAG_HOME/models"

python3.12 -m venv "$RAG_VENV"
source "$RAG_VENV/bin/activate"
python -m pip install --upgrade pip
~~~

### 可复现 CPU 环境

这与仓库锁定环境一致，适合先做功能核验：

~~~bash
python -m pip install --index-url https://download.pytorch.org/whl/cpu \
  'torch==2.8.0'
python -m pip install -r "$REPO/rag/requirements.lock.txt"
~~~

锁定文件包含 `torch==2.8.0+cpu`、`sentence-transformers==5.7.0`、`transformers==4.57.6` 和 `numpy==2.5.3`。如果远程环境必须使用 CUDA 版 PyTorch，应先按驱动支持的 CUDA 版本安装对应官方 wheel，再安装其余依赖，并确认 `embedding.py` 当前仍显式选择 CPU；不要把 CUDA wheel 和锁定的 CPU wheel 混装。

检查环境：

~~~bash
"$RAG_VENV/bin/python" -X utf8 - <<'PY'
import torch, numpy, sentence_transformers, transformers
print('torch:', torch.__version__)
print('cuda available:', torch.cuda.is_available())
print('numpy:', numpy.__version__)
print('sentence-transformers:', sentence_transformers.__version__)
print('transformers:', transformers.__version__)
PY
~~~

## 4. 安装或复制本地模型

联网时，使用仓库脚本下载并生成 SHA256 manifest。Embedding revision 固定为 `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`：

~~~bash
export RAG_PY="$RAG_VENV/bin/python"
export EMBED_DIR="$RAG_HOME/models/Qwen3-Embedding-0.6B"
export RERANK_DIR="$RAG_HOME/models/Qwen3-Reranker-0.6B"

HF_HUB_DISABLE_XET=1 "$RAG_PY" -X utf8 -B \
  "$REPO/rag/download_model.py" \
  --output "$EMBED_DIR" \
  --revision 97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3

HF_HUB_DISABLE_XET=1 "$RAG_PY" -X utf8 -B \
  "$REPO/rag/download_reranker.py" \
  --output "$RERANK_DIR" \
  --revision e61197ed45024b0ed8a2d74b80b4d909f1255473
~~~

Reranker 不是 BM25 或普通 hybrid 的必需项，只在第四组对照中使用。若远程不能访问 Hugging Face，可以从已经安装好的主机复制整个模型目录，必须保留对应的 `rag-model-manifest.json` 或 `rag-reranker-manifest.json`；worker 会在启动时逐文件核验，不接受缺少 manifest 或校验不一致的目录。

## 5. 创建远程 RAG runtime

`runtime.local.json` 被 `.gitignore` 忽略，只在远程主机创建。以下路径按实际磁盘位置修改：

~~~bash
cat > "$REPO/rag/runtime.local.json" <<JSON
{
  "registry": "rag/releases/ug1399-2026.1-en-curated.json",
  "python": "$RAG_PY",
  "model": "$EMBED_DIR",
  "reranker": "$RERANK_DIR"
}
JSON
cat "$REPO/rag/runtime.local.json"
~~~

BM25 不会加载 Embedding 模型，但保留 `model` 路径可以让同一份 runtime 同时用于四组实验。真正的 hybrid 必须有本地 Embedding 路径；启用 Reranker 的 policy 必须有本地 Reranker 路径。

验证发布注册表、语料、索引和模型：

~~~bash
cd "$REPO"
python3 -B -c "from rag.release import load_release, verify_release; verify_release(load_release('rag/releases/ug1399-2026.1-en-curated.json')); print('release OK')"

"$RAG_PY" -X utf8 -B -m rag.audit \
  --corpus rag/corpora/ug1399-2026.1-en-curated \
  --index rag/indexes/ug1399-qwen06b-2026.1-curated \
  --model "$EMBED_DIR" \
  --pdf docs/ug1399-vitis-hls-en-us-2026.1.pdf \
  --version 2026.1 \
  --output "rag/.cache/audit-remote-$(date +%Y%m%dT%H%M%SZ).json"
~~~

## 6. 离线检索 smoke test

下载完成后可断网运行。worker 会设置 `HF_HUB_OFFLINE=1` 和 `TRANSFORMERS_OFFLINE=1`，不会自动联网或替换模型：

~~~bash
cd "$REPO"
"$RAG_PY" -X utf8 -B -m rag.retrieve \
  'pragma HLS array_partition cyclic factor dim' \
  --mode bm25 --profile fix_first

"$RAG_PY" -X utf8 -B -m rag.retrieve \
  'Can a recursive function be synthesized?' \
  --mode hybrid --profile fix_first \
  --top-k 3 --max-bytes 6000
~~~

预期结果：BM25 能命中 `array_partition` 等词项；hybrid 能返回带 `source.file`、章节和页码的 2026.1 条目。若出现 `rag_configuration_error`，优先检查 runtime 中的绝对路径、模型 manifest 和注册表指纹。

运行本地协议测试：

~~~bash
"$RAG_PY" -X utf8 -B -m unittest rag.test_retrieval -v
python3 -m unittest tools.test_rag_agent tools.test_rag_query -v
~~~

## 7. 配置远程生成服务和 Vitis

不要直接使用仓库里的 Windows `serve/runtime.local.json`。在 Ubuntu 上复制模板并改成 Linux 路径：

~~~bash
cp "$REPO/serve/runtime.json" "$REPO/serve/runtime.local.json"
cd "$REPO"
"$RAG_PY" -X utf8 - <<'PY'
import json
from pathlib import Path

path = Path('serve/runtime.local.json')
data = json.loads(path.read_text())
data['model']['base_url'] = 'http://127.0.0.1:8000/v1'
data['model']['name'] = 'qwen38'
data['hls']['vitis_root'] = '/tools/Xilinx/Vitis/2026.1'
data['hls']['vivado_root'] = '/tools/Xilinx/Vivado/2026.1'
data['hls']['license_file'] = '/path/to/Xilinx.lic'
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
PY
~~~

如果生成服务在另一台机器，把 `base_url` 改为可达的 HTTPS 或内网地址，并先用 `curl` 检查 `/v1/models`。许可证路径可以设为 `null`，前提是 Vitis 环境已通过 `XILINXD_LICENSE_FILE` 等方式配置：

~~~bash
export XILINXD_LICENSE_FILE=/path/to/Xilinx.lic
source /tools/Xilinx/Vitis/2026.1/settings64.sh
source /tools/Xilinx/Vivado/2026.1/settings64.sh
vitis_hls -version
~~~

## 8. 先跑单任务，再跑 Bench4HLS

先用 `Prob001` 验证生成、检索和 Vitis 链路。每次使用全新的输出目录，禁止覆盖旧 receipt：

~~~bash
cd "$REPO"
TASK=data/processed/bench4hls/Prob001
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="output/remote_smoke/$STAMP/hybrid"
mkdir -p "$OUT"

"$RAG_PY" -X utf8 -B -m agent.interface.entry \
  "$TASK/problem.txt" "$OUT" \
  --config serve/runtime.local.json \
  --task-manifest "$TASK/task.json" \
  --policy agent/config/policy.rag-hybrid.json \
  --rag-runtime rag/runtime.local.json
~~~

检查 `OUT/result.json`、`OUT/candidates/*/retrieval.json` 和 `OUT/candidates/*/retrieval.log`。`retrieval.json` 必须包含 query、release/index 指纹、召回 ID、实际注入字节数和 reranker 配置。

当前 `tools/run_bench4hls_compare.py` 只透传默认 Agent policy，不能直接用它完成 off/BM25/hybrid/rerank 四条件因果对照。下面的循环适合远程批量 smoke 或收集每个条件的 receipt；正式比较仍需固定同一首稿、同一反馈和同一预算：

~~~bash
cd "$REPO"
DATASET="$REPO/data/processed/bench4hls"
RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)
ROOT_OUT="$REPO/output/bench4hls_rag/$RUN_ID"
mkdir -p "$ROOT_OUT"

run_condition() {
  local condition="$1"
  local policy=""
  if [[ $# -ge 2 ]]; then policy="$2"; fi
  local condition_out="$ROOT_OUT/$condition"
  mkdir -p "$condition_out"

  for task_dir in "$DATASET"/Prob*; do
    [[ -f "$task_dir/task.json" ]] || continue
    local name
    name=$(basename "$task_dir")
    local out="$condition_out/$name"
    [[ -e "$out" ]] && { echo "skip existing $out"; continue; }
    mkdir -p "$out"
    set +e
    if [[ -n "$policy" ]]; then
      "$RAG_PY" -X utf8 -B -m agent.interface.entry \
        "$task_dir/problem.txt" "$out" \
        --config serve/runtime.local.json \
        --task-manifest "$task_dir/task.json" \
        --policy "$policy" \
        --rag-runtime rag/runtime.local.json \
        > "$out/agent.stdout.log" 2>&1
    else
      "$RAG_PY" -X utf8 -B -m agent.interface.entry \
        "$task_dir/problem.txt" "$out" \
        --config serve/runtime.local.json \
        --task-manifest "$task_dir/task.json" \
        > "$out/agent.stdout.log" 2>&1
    fi
    local rc=$?
    set -e
    echo "$rc" > "$out/exit_code"
    echo "$condition $name exit=$rc"
  done
}

run_condition off
run_condition bm25 agent/config/policy.rag-bm25.json
run_condition hybrid agent/config/policy.rag-hybrid.json
run_condition hybrid_rerank agent/config/policy.rag-hybrid-rerank.json
~~~

先用 `find "$DATASET" -name task.json | wc -l` 确认任务数量。若只想跑一个任务，把循环改为 `for task_dir in "$DATASET"/Prob001; do`；不要在第一次就并发跑完整数据集，以免同时启动过多 Vitis 进程和生成请求。

## 9. 汇总 receipt

下面脚本只读取每个条件实际产生的 `result.json`，分别统计编译、csim/run、综合和 overall：

~~~bash
"$RAG_PY" -X utf8 - <<'PY'
import json
from pathlib import Path

root = Path('output/bench4hls_rag')
for batch in sorted(root.iterdir() if root.is_dir() else []):
    print('batch:', batch.name)
    for condition in ('off', 'bm25', 'hybrid', 'hybrid_rerank'):
        rows = []
        for path in sorted((batch / condition).glob('*/result.json')):
            try:
                rows.append(json.loads(path.read_text()))
            except (OSError, ValueError):
                pass
        n = len(rows)
        def passed(key):
            return sum((r.get('checks') or {}).get(key) == 'passed' for r in rows)
        overall = sum((r.get('checks') or {}).get('run') == 'passed' and
                      (r.get('checks') or {}).get('synthesize') == 'passed' for r in rows)
        print(f'  {condition:14} n={n:3} compile={passed("compile"):3} '
              f'csim={passed("run"):3} synth={passed("synthesize"):3} overall={overall:3}')
PY
~~~

同时保存以下文件，后续才能分析 RAG 是否真正帮助修复：

~~~text
output/bench4hls_rag/<batch>/<condition>/<task>/result.json
output/bench4hls_rag/<batch>/<condition>/<task>/candidates/*/retrieval.json
output/bench4hls_rag/<batch>/<condition>/<task>/candidates/*/retrieval_input.json
output/bench4hls_rag/<batch>/<condition>/<task>/candidates/*/retrieval_output.json
output/bench4hls_rag/<batch>/<condition>/<task>/candidates/*/retrieval.log
~~~

不要把 `retrieval.json` 中的“召回”直接当成“模型看到”：只有 `injected_ids` 和 `injected_bytes` 代表真正进入生成上下文的资料。也不要用 `FakeValidator` 或没有真实 Vitis 的本地 smoke 结果宣称修复成功率提升。

## 10. 常见故障

| 现象 | 检查 |
| --- | --- |
| `rag_configuration_error` | `rag/runtime.local.json` 是否为绝对路径；registry 是否指向 2026.1；policy 的 mode/profile 是否合法。 |
| `Local model file checksum mismatch` | 模型目录是否完整复制；不要复制 Hugging Face 缓存目录代替 manifest 目录。 |
| `Hybrid RAG requires an explicit local model path` | hybrid policy 必须配置 `model`；BM25 可以不加载模型。 |
| `RAG reranker is enabled but no local reranker path was supplied` | rerank policy 必须配置 `reranker`，并保留 `rag-reranker-manifest.json`。 |
| `rag_timeout` | 先单任务运行；检查 CPU 负载、模型冷启动时间和 `rag_timeout_seconds`。不要静默降级为无 RAG。 |
| `vitis_hls: command not found` | 重新 source Vitis/Vivado 2026.1 的 `settings64.sh`，并确认 `vitis_root`、`vivado_root` 是 Linux 路径。 |
| 生成请求失败 | `curl <base_url>/models`；检查 vLLM 端口、防火墙、TLS 和模型名。 |
| 任务之间互相覆盖 | 每次批次使用新的 UTC 时间戳目录；不要复用已有 task 输出目录。 |

## 11. 回传结果

实验完成后，将以下内容压缩或上传到项目的 `output/` 对应批次目录：

~~~bash
tar -czf "bench4hls-rag-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" \
  output/bench4hls_rag/<batch> \
  rag/.cache/audit-remote-*.json
~~~

同时记录：Ubuntu 版本、Vitis/Vivado 版本、器件与时钟、生成模型地址和模型名、四组 policy、任务数量、失败任务、每组 compile/csim/synthesis/overall 计数，以及 Git commit SHA。不要上传模型权重、许可证文件或包含隐藏测试答案的日志。
