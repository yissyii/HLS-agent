# 远程 Claude：UG1399 fix cards 对照评测计划

> 说明：`docs/handoff/environment.md` 描述的是远程 Ubuntu/5090D 主机（`/root/AMDCmpt/zcomp`）的运行环境，不是 Windows 本机环境。本机只负责维护代码、提交和推送；以下命令均在远程 Ubuntu 仓库内执行。

## 目标与结论边界

本轮在远程 Ubuntu 的真实 Vitis HLS 2026.1 环境中，评估首批 18 条高精度 fix cards 对 Bench4HLS 修复阶段的作用。实验复用同一批冻结首稿，分别测量：

1. 完整 UG1399 `general` release；
2. 独立 fix-card release；
3. 独立 fix-card release + Qwen3 reranker。

检索 Hit@1、人工编写的 18 条 smoke query，以及 `human_reviewed` 状态都不能当作修复成功率。最终结论只依据固定首稿、相同反馈与预算、真实 C simulation / synthesis 的逐题配对结果。

远程任务只运行评测并写入 `output/` 和评测报告。不要修改或重建 `rag/corpora/`、`rag/indexes/`、`rag/releases/`、fix cards、Agent 策略源码或数据集。发现发布哈希、工具链、模型清单或服务异常时停止，不要静默降级。

## 1. 同步并冻结实验版本

```bash
cd /root/AMDCmpt/zcomp
git switch feat/rag
git pull --ff-only origin feat/rag
git status --short
git rev-parse HEAD
```

`git status --short` 必须为空。把 `git rev-parse HEAD` 的结果记为 `target_commit`；之后若代码、policy、release 或数据集发生变化，本轮结果作废并从预检重新开始。

## 2. 加载远程工具链和服务

```bash
source /tools/Xilinx/2026.1/Vitis/settings64.sh
source /tools/Xilinx/2026.1/Vivado/settings64.sh
export XILINXD_LICENSE_FILE=/root/.Xilinx/Xilinx.lic
export PYTHONUTF8=1
export PYTHONDONTWRITEBYTECODE=1
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false

command -v vitis_hls
test -f "$XILINXD_LICENSE_FILE"
curl -fsS http://127.0.0.1:8001/v1/models | python3 -m json.tool
```

模型服务必须返回 `qwen38`。评测配置固定为 `serve/runtime.rag-eval.json`：`temperature=0.0`、生成服务 `http://127.0.0.1:8001/v1`、part `xczu3eg-sbva484-1-e`、clock 5 ns。不得改用 `serve/runtime.local.json`。

## 3. 创建本机私有 runtime 与 policy

以下文件名匹配 `.gitignore` 中的 `rag/*.local.json`，仅供远程实验使用。

```bash
cat > rag/runtime.general.remote.local.json <<'JSON'
{
  "registry": "rag/releases/ug1399-2026.1-en-curated.json",
  "python": "/root/hls-rag/venv/bin/python",
  "model": "/root/hls-rag/models/Qwen3-Embedding-0.6B",
  "reranker": null
}
JSON

cat > rag/runtime.fix.remote.local.json <<'JSON'
{
  "registry": "rag/releases/ug1399-2026.1-fix-cards-v1.json",
  "python": "/root/hls-rag/venv/bin/python",
  "model": "/root/hls-rag/models/Qwen3-Embedding-0.6B",
  "reranker": null
}
JSON

cat > rag/runtime.fix-rerank.remote.local.json <<'JSON'
{
  "registry": "rag/releases/ug1399-2026.1-fix-cards-v1.json",
  "python": "/root/hls-rag/venv/bin/python",
  "model": "/root/hls-rag/models/Qwen3-Embedding-0.6B",
  "reranker": "/root/hls-rag/models/Qwen3-Reranker-0.6B"
}
JSON

cp agent/config/policy.rag-hybrid.json rag/policy.general.remote.local.json
cp agent/config/policy.rag-hybrid.json rag/policy.fix.remote.local.json
cp agent/config/policy.rag-hybrid-rerank.json rag/policy.fix-rerank.remote.local.json

python3 - <<'PY'
import json
from pathlib import Path

for name, profile in [
    ('rag/policy.general.remote.local.json', 'fix_first'),
    ('rag/policy.fix.remote.local.json', 'all'),
    ('rag/policy.fix-rerank.remote.local.json', 'all'),
]:
    path = Path(name)
    data = json.loads(path.read_text(encoding='utf-8'))
    data['rag_profile'] = profile
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
PY
```

fix-card release 内 18 条记录全部属于 `fix`，因此两份 fix policy 必须使用 `rag_profile=all`。`fix_first` 是同一 corpus 同时含 `fix/general` 时的排序先验，不用于表达跨 release 联合检索；当前 Agent 每次只读取一个 release。

## 4. 发布、模型和测试预检

```bash
python3 -B - <<'PY'
from rag.release import load_release, verify_release

for path in (
    'rag/releases/ug1399-2026.1-en-curated.json',
    'rag/releases/ug1399-2026.1-fix-cards-v1.json',
):
    release = load_release(path)
    verify_release(release)
    print(release['release_id'], release['records_sha256'], release['index_fingerprint'])
PY

/root/hls-rag/venv/bin/python -X utf8 -B -m unittest \
  rag.test_fix_cards rag.test_retrieval tools.test_rag_query tools.test_rag_compare -v

/root/hls-rag/venv/bin/python -X utf8 -B -m rag.retrieve \
  'no matching function for call to object of type ap_uint; x(0) bit selection' \
  --corpus rag/corpora/ug1399-2026.1-fix-cards-v1 \
  --index rag/indexes/ug1399-qwen06b-2026.1-fix-cards-v1 \
  --model /root/hls-rag/models/Qwen3-Embedding-0.6B \
  --mode hybrid --profile all --top-k 2 --max-bytes 2400
```

这里必须同时核验两个 release 的 corpus/index/vectors 哈希。Embedding 与 reranker 目录内的 manifest 会在 Agent 冻结输入和加载模型时继续校验；若模型 revision、文件 SHA256 或 encoder identity 不符，立即停止。

## 5. 六题 smoke

先按下列顺序跑 `Prob016/041/045/071/104/152`。这些题覆盖两个已知真命中、三个历史假匹配方向和一个普通语法错误。每题先跑 general，再以 general 产出的批目录作为 `--drafts-from` 跑 fix 和 fix + reranker。

```bash
mkdir -p output/remote-fix-card-eval

for task in Prob016 Prob041 Prob045 Prob071 Prob104 Prob152; do
  /root/hls-rag/venv/bin/python -X utf8 -B tools/run_rag_compare.py \
    --task "$task" --workers 1 \
    --config serve/runtime.rag-eval.json \
    --policy rag/policy.general.remote.local.json \
    --rag-runtime rag/runtime.general.remote.local.json \
    2>&1 | tee "output/remote-fix-card-eval/${task}-general.log"
done
```

`tools/run_rag_compare.py` 会为每次调用创建并在最后打印一个 `output/.../rag_compare_*` 批目录。逐题记录该 general 批目录，然后执行：

```bash
/root/hls-rag/venv/bin/python -X utf8 -B tools/run_rag_compare.py \
  --task ProbNNN --workers 1 \
  --config serve/runtime.rag-eval.json \
  --policy rag/policy.fix.remote.local.json \
  --rag-runtime rag/runtime.fix.remote.local.json \
  --drafts-from GENERAL_BATCH_DIR

/root/hls-rag/venv/bin/python -X utf8 -B tools/run_rag_compare.py \
  --task ProbNNN --workers 1 \
  --config serve/runtime.rag-eval.json \
  --policy rag/policy.fix-rerank.remote.local.json \
  --rag-runtime rag/runtime.fix-rerank.remote.local.json \
  --drafts-from GENERAL_BATCH_DIR
```

smoke 通过条件：

- 三组的 `initial_source_sha256` 一致且 `initial_source_verified=true`；
- 没有 `runner_error`、超时、release/model mismatch 或缺失 checks；
- Prob071/104/152 等不适用诊断允许 `no_reference_injected`，不能为了满足 Top-K 强制注入；
- 检索发生时，`retrieval.json` 中 `eligible_count`、`rejected_count`、`selection_audit` 和 `no_reference_reason` 与实际注入一致；
- 每个候选目录保留 `retrieval_input.json`、`retrieval.json`、`context.json`、`prompt.txt`，任务目录保留 `result.json` 和 `events.jsonl`。

任何一项不满足，先写明失败原因并停止全量评测。

## 6. 全量固定首稿评测

全量 170 题使用 `workers=1`，避免并发工具链或模型争用改变时序。先运行 general 组并从终端最后一行取得 `GENERAL_BATCH_DIR`：

```bash
/root/hls-rag/venv/bin/python -X utf8 -B tools/run_rag_compare.py \
  --workers 1 \
  --config serve/runtime.rag-eval.json \
  --policy rag/policy.general.remote.local.json \
  --rag-runtime rag/runtime.general.remote.local.json \
  2>&1 | tee output/remote-fix-card-eval/general-full.log
```

随后两组必须直接复用该目录中的首稿：

```bash
/root/hls-rag/venv/bin/python -X utf8 -B tools/run_rag_compare.py \
  --workers 1 \
  --config serve/runtime.rag-eval.json \
  --policy rag/policy.fix.remote.local.json \
  --rag-runtime rag/runtime.fix.remote.local.json \
  --drafts-from GENERAL_BATCH_DIR \
  2>&1 | tee output/remote-fix-card-eval/fix-full.log

/root/hls-rag/venv/bin/python -X utf8 -B tools/run_rag_compare.py \
  --workers 1 \
  --config serve/runtime.rag-eval.json \
  --policy rag/policy.fix-rerank.remote.local.json \
  --rag-runtime rag/runtime.fix-rerank.remote.local.json \
  --drafts-from GENERAL_BATCH_DIR \
  2>&1 | tee output/remote-fix-card-eval/fix-rerank-full.log
```

每个 batch 都会含 `off/bm25/hybrid` 三列。主要对比使用各 batch 的 `hybrid`；general batch 的 `off` 是共同基线。fix-rerank batch 中 BM25 也会经过 reranker，因此报告中把它明确命名为 `fix BM25 + reranker`，不要与无重排 BM25 混为一组。三次运行中重复出现的 off 只用于一致性审计。

## 7. 审计与报告

在 `report/evaluations/` 新建一份日期化报告，至少记录：

- `target_commit`、两个 release ID、records/index/vectors 指纹、模型 revision；
- Vitis/Vivado 版本、part、clock、反馈策略、temperature、修复次数、检索预算；
- 有效冻结首稿数与排除原因；
- general hybrid、fix hybrid、fix hybrid + reranker 的 compile / csim(run) / synthesis / overall；
- 各组相对共同 off 的 paired wins / losses / ties；
- 首稿失败后的 recovery 分子、分母和恢复率；
- 每组 `exact_actionable`、`applicable_rule`、`partial`、`topic_only`、`wrong_direction`、`no_evidence` 数量；
- `no_reference_injected` 数量，以及按 `no_reference_reason` 分类的数量；
- `eligible_count`、`rejected_count`、实际注入条数与字节数的分布；
- 18 张 fix cards 中实际被召回、通过签名门、注入、参与胜例、参与负例的卡片 ID；
- 将“18 张卡可覆盖的错误族”和“纯 C/C++、功能逻辑、语料未覆盖错误”分开统计。

逐题检查的证据以 `context.json` 和 `prompt.txt` 中模型实际看到的 `<REFERENCE_MATERIAL>` 为准；只出现在召回列表、但被门或预算拒绝的条目不算已注入。胜例也不能直接归因于卡片：报告需对照 off 的候选 diff、诊断和注入证据，标为“支持归因”“可能相关”或“无证据”。

报告完成后只提交报告；`output/`、三份 runtime、三份 policy 保持 gitignored。提交前执行：

```bash
git status --short
git diff --check
git add report/evaluations/<报告文件名>.md
git commit -m 'docs(rag): 记录 fix card 远程对照评测'
```

不要在远程结果出来后自行升级卡片的 `validation.status`。单次 Bench4HLS 命中或修复成功不能替代逐卡可复现的独立 Vitis 验证。

## 停止条件

遇到以下任一情况，保留当前 `summary.json` 和日志，报告为基础设施失败并停止后续组：

- 工作区在冻结后发生变化，或三个组的首稿 SHA256 不一致；
- release、corpus、index、vectors 或模型 manifest 校验失败；
- `vitis_hls`、Vivado、license 或 `qwen38` 服务不可用；
- 评测配置不是 `temperature=0.0`，或真实 Vitis checks 缺失；
- fix-only policy 不是 `rag_profile=all`；
- runner 出错、检索静默降级、候选证据文件缺失或 summary 状态不完整。
