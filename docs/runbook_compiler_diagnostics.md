# Runbook：在 Ubuntu 本地 vLLM 上跑 Bench4HLS `compiler_diagnostics` 实验

> 给 Ubuntu 机器上的 Claude：按下面步骤在**本地 vLLM（127.0.0.1:8001，不走 ngrok）**上重跑 Bench4HLS 的 baseline vs agent 对比，agent 侧用新档 `compiler_diagnostics`。目标是回答：把编译/综合的具体错误喂给模型后，修复率相比 `category_only` 基线是否提升。

## 背景（已在本机完成，你只需在 Ubuntu 复现）

- 代码已推送到 `origin/feat/hls-rag`，commit `ed23201`（“精细化诊断：Vitis 日志按 kind 分级提取，feedback_policy 新增 compiler_diagnostics 档”）。
- 新功能分两层，别混淆：
  - **提取**（程序自动）：`evaluation/diagnostics.py` 把 Vitis 日志按 kind 分级成 `compiler/synthesis/functional/environment/license/timeout` 条目，含 clang 源码行 + caret + 去重 note。
  - **释放**（人配置）：任务清单里 `feedback_policy` 决定哪些 kind 进 prompt。三档：
    - `category_only`（默认，只给报错类别）
    - `compiler_diagnostics`（**本次实验**：放 `compiler`+`synthesis` 的具体错误，扣下 `functional` 的 Mismatch 反例）
    - `public_diagnostics`（原始 `diagnostic_tail`，会泄露隐藏测试，别用于正式对比）
- 注意：`compiler_diagnostics` **只对编译/综合错误有增益**，对功能错误（functional_or_runtime_error）它和 category_only 效果一样（compiler_text 为空会回退到类别串）。

## 前置条件（先自检）

1. Vitis HLS 2026.1 已装，知道 `vitis_root` / `vivado_root` 的绝对路径。
2. vLLM 正在 `127.0.0.1:8001` 服务模型，用 `curl http://127.0.0.1:8001/v1/models` 确认模型名（`name` 字段要和下面配置一致）。
3. Python 环境能跑 `python -B tools/...`。

## 步骤

### 1. 拉代码
```bash
cd <repo路径>
git fetch origin
git checkout feat/hls-rag
git log --oneline -1          # 应看到 ed23201 精细化诊断...
ls evaluation/diagnostics.py tools/test_diagnostics.py   # 都应存在
```

### 2. 自测（确认代码在 Ubuntu 上可跑）
```bash
python -B tools/test_diagnostics.py      # 9 项全过
python -B tools/test_agent_contract.py   # 协议测试通过
```

### 3. 写本地配置 `serve/runtime.local.json`（gitignored，不会被覆盖）

关键：`base_url` 用 `http://127.0.0.1:8001/v1`（loopback HTTP，配置校验允许），`name` 用第 1 步查到的模型名，`vitis_root`/`vivado_root` 填本机路径。

```json
{
  "model": {
    "base_url": "http://127.0.0.1:8001/v1",
    "name": "qwen38",
    "max_tokens": 4096,
    "temperature": 0.7,
    "enable_thinking": false,
    "timeout_seconds": 180,
    "context_tokens": 16384,
    "context_note": "local vLLM for compiler_diagnostics experiment",
    "tls_sha256": ""
  },
  "hls": {
    "vitis_root": "/path/to/2026.1/Vitis",
    "vivado_root": "/path/to/2026.1/Vivado",
    "license_file": null,
    "part": "xczu3eg-sbva484-1-e",
    "clock_ns": 5,
    "csim_timeout_seconds": 120,
    "synthesis_timeout_seconds": 300,
    "total_timeout_seconds": 600
  }
}
```

### 4. 验证端点
```bash
python -B tools/check_endpoint.py    # 应打印 HTTP 200 + 模型回复
```

### 5. 准备数据 + 设 `feedback_policy=compiler_diagnostics`
```bash
# 数据还没的话先 ingest（下载 Bench4HLS 快照并生成 170 个 task 目录）
python -B tools/ingest_bench4hls.py

# 把所有 task.json 的 feedback_policy 改成 compiler_diagnostics
python -B - <<'PY'
import json
from pathlib import Path
for f in Path("data/processed/bench4hls").glob("*/task.json"):
    d = json.loads(f.read_text(encoding="utf-8"))
    d["feedback_policy"] = "compiler_diagnostics"
    f.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("feedback_policy=compiler_diagnostics set on", len(list(Path("data/processed/bench4hls").glob("*/task.json"))), "tasks")
PY
```

### 6. 定范围
- **全量 170 题**（对齐 9-17 的 category_only 基线）——移走 selection.json：
  ```bash
  mv data/processed/bench4hls/selection.json data/processed/bench4hls/selection.hard50.json
  ```
- 只想先跑 **50 题 Hard 子集**：保留 selection.json 不动即可。

### 7. 冒烟（1 题，验证链路 + 新档反馈真进 prompt）
```bash
python -B -m agent.interface.entry \
  data/processed/bench4hls/Prob065/problem.txt output/smoke/Prob065 \
  --config serve/runtime.local.json \
  --task-manifest data/processed/bench4hls/Prob065/task.json
```
然后确认两件事：
```bash
# (a) validation.json 里 compiler_text 非空（说明提取链路生效）
python -c "import json,glob; [print(json.load(open(f,encoding='utf-8'))['outcome']['compiler_text'][:120]) for f in glob.glob('output/smoke/Prob065/attempt_*/result/candidates/*/validation.json')]"
# (b) 修复请求里有 <STAGE>repair 且含根因文本（Prob065 的根因是 "requires 2 arguments"）
grep -rl "requires 2 arguments" output/smoke/Prob065/ | grep -i request
```
冒烟任务本身过不过无所谓，关键是端点能调通、`compiler_text` 能进 repair prompt。

### 8. 正式跑（建议 tmux/nohup 后台）
```bash
python -B tools/run_bench4hls_compare.py \
  --config serve/runtime.local.json --workers 4 \
  2>&1 | tee output/compare_compiler_diagnostics.log
```
- `--workers` 对齐 vLLM 的 `--max-num-seqs`（默认 4）。
- 170 题 × baseline/agent = 340 次调用，粗估 1.5~2.5 小时；结果汇总在 `output/compare_<时间戳>/summary.json`。
- 网络故障会自动整轮重跑，不用手动干预。

### 9. 汇报
跑完读 `output/compare_<时间戳>/summary.json` 的 `comparison`，汇报：
1. baseline 与 agent 的 `compile / run / synthesize / overall` 四个通过率（及 delta）。
2. 对照 category_only 基线（9-17 全量 170：agent overall 54.7%，baseline 40.0%）——看 `compiler_diagnostics` 是否把 agent 的 overall 再往上抬。

## 验收信号
- 冒烟里 repair 请求含编译器根因文本 → 新档生效。
- 全量跑完：agent overall > baseline，且与 category_only 基线对比可看出 compiler_diagnostics 的增量（预期增量集中在**编译/综合失败**的题上，功能失败题不变）。
