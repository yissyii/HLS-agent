# 远程 Ubuntu/5090D 运行环境（工作交接）

> 最后更新：2026-09-21 · 分支 `feat/rag`

## 一句话概览

**这是远程 Ubuntu/5090D 主机的运行环境说明。** 该主机通过 Windows 侧的 Claude Code 访问；真正的开发、Vitis HLS、RAG 检索、模型推理全部发生在 **WSL Ubuntu** 内。Windows 主机只是访问层（通过 UNC 路径读写 WSL 文件系统）。远程主机 GPU 为 RTX 5090 D，运行 Vitis HLS 2026.1 和本地 vLLM `qwen38`。

## 1. 机器与系统

| 项 | 值 |
|---|---|
| 主机系统 | Windows 11 Home China（10.0.22621） |
| WSL | Ubuntu 22.04.5 LTS（WSL2，另有 `docker-desktop` 已停止） |
| GPU | NVIDIA GeForce RTX 5090 D（32 GB） |
| 磁盘 | `/root` 分区 1007G，已用 313G，可用 644G |

## 2. 目录与访问路径

| 内容 | 路径 |
|---|---|
| 仓库（Windows 侧 UNC） | `\\wsl.localhost\Ubuntu-22.04\root\AMDCmpt\zcomp` |
| 仓库（WSL 内） | `/root/AMDCmpt/zcomp` |
| RAG 独立工具/venv/模型/脚本 | `/root/hls-rag/` |
| Vitis HLS 2026.1 | `/tools/Xilinx/2026.1/Vitis` |
| Vivado 2026.1 | `/tools/Xilinx/2026.1/Vivado` |
| Vitis license | `/root/.Xilinx/Xilinx.lic` |

## 3. Git 与 SSH（⚠️ 最关键坑）

| 项 | 值 |
|---|---|
| remote | `git@github.com:yissyii/HLS-agent.git` |
| 当前分支 | `feat/rag`（默认 `main`） |
| 提交身份 | `mxHanks <mxHanks@users.noreply.github.com>` |
| SSH 私钥 | **WSL**：`/root/.ssh/id_ed25519` |
| SSH config | `/root/.ssh/config` 把 `github.com` 走 `ssh.github.com:443` |

- ⚠️ **Windows 侧 `C:\Users\Administrator\.ssh\` 没有私钥**，所以直接在 Windows 的 Git Bash 里跑 `git push` / `git pull` / `git fetch` 会报 `Permission denied (publickey)`。
- ✅ **正确做法**：远端操作一律进 WSL 执行：

```bash
wsl.exe -d Ubuntu-22.04 -- bash -lc 'cd /root/AMDCmpt/zcomp && git push origin feat/rag'
```

- Windows 里可以正常 `git add` / `git commit` / `git status` / `git log`，只有涉及远端（push/pull/fetch）要走 WSL。
- 若想让 Windows 的 git 直接可用：把 `/root/.ssh/id_ed25519` + `config` 复制到 `C:\Users\Administrator\.ssh\`。

## 4. Python 环境

| 环境 | 版本 / 路径 | 用途 |
|---|---|---|
| Windows 系统 | Python 3.13.5（`C:\Python313`） | Claude Code 的 Bash 工具默认用它；**默认 GBK 编码**，读文件要显式 `encoding='utf-8'`，控制台报 `UnicodeEncodeError` 时加 `PYTHONIOENCODING=utf-8` |
| WSL 系统 | Python 3.10.12 | — |
| RAG 独立 venv | `/root/hls-rag/venv/bin/python` | torch 2.8.0+cpu、transformers 4.57.6、sentence-transformers 5.7.0 |

## 5. 模型与服务

| 项 | 值 |
|---|---|
| 生成模型 | `qwen38`，本地 vLLM @ `http://127.0.0.1:8001/v1`（vLLM 0.25.1-sm120-cu133；`max_model_len` 16384；采样 `temperature=0.7`，`max_tokens` 4096） |
| Embedding | `/root/hls-rag/models/Qwen3-Embedding-0.6B`（本地文件加载，CPU） |
| Reranker | `/root/hls-rag/models/Qwen3-Reranker-0.6B`（本地文件加载，CPU） |
| RAG 路径配置 | `rag/runtime.local.json`（gitignore，指向上述 venv/models） |

## 6. Vitis HLS

- 版本 **2026.1**，part `xczu3eg-sbva484-1-e`，时钟 5 ns。
- `vitis_hls` **不在默认 PATH**，使用前先加载环境脚本：

```bash
source /tools/Xilinx/2026.1/Vitis/settings64.sh
```

## 7. 近期工作关键路径

| 内容 | 路径 |
|---|---|
| 测评产物 | `output/bench4hls_rag/run01/hybrid_rerank/`（170 题，`result.json`/`retrieval*.json`/`prompt.txt`） |
| 测评报告 | `report/evaluations/`（`9-20-…-rerank.md`、`9-21-…-error-annotation.md`） |
| 筛查计划 | `report/design/remote_claude_rag_screening.md` |
| 语料/索引/注册表 | `rag/corpora/ug1399-2026.1-en-curated`、`rag/indexes/ug1399-qwen06b-2026.1-curated`、`rag/releases/ug1399-2026.1-en-curated.json` |
| 标注（发布种子） | `rag/annotations/`（`bench4hls-rerank-v1.jsonl`、`README.md` 为评分规则） |
| 标注（复核草稿） | `rag/staging/claude-screening/bench4hls-run01-hybrid_rerank/` |
| 运行/交接指南 | `rag/UBUNTU_REMOTE_GUIDE.md`、`rag/HANDOFF.md` |

## 8. 已知注意事项

1. **`rag/staging/` 在 `.gitignore`（第 14 行）**，默认不入库；本次筛查产物用 `git add -f` 强制纳入了 `claude-screening/bench4hls-run01-hybrid_rerank/` 下的 7 个文件（已 tracked），其余 staging 文件仍被忽略。
2. **Windows 侧脚本读 JSON 必须 `encoding='utf-8'`**，否则 `UnicodeDecodeError: 'gbk'`。
3. **`rag/runtime.local.json` 也是 gitignore**（本机私有路径），远程/新机需按 `rag/UBUNTU_REMOTE_GUIDE.md` 自建。
4. 活动库（corpus/release/index/policy）按 `remote_claude_rag_screening.md` 约定**只做注释分析、不修改**；人工复核后才可发布到 `rag/annotations/`。
