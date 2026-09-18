#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Download publicly available PDFs for the SAGE-HLS reference list.

Reference numbering matches HLS代码智能体相关文献整理.docx ([n] entries);
entry 00 is the source paper itself (arXiv 2508.03558).

Only the standard library is used. Safe to re-run: files that already exist
and validate are skipped.
"""
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "sage-hls-refs/1.0 (personal research)"
TIMEOUT = 90
BACKOFF = (5, 15, 30)
ARXIV_SLEEP = 3
MIN_BYTES = 20 * 1024

# (num, slug, kind, url_or_arxiv_id, note)
#   kind: "arxiv"  -> https://arxiv.org/pdf/<id>
#         "direct" -> plain PDF URL
#         "dspace" -> PDF wrapped in a multipart/form-data envelope
#         "none"   -> no public full text; manifest-only
ENTRIES = [
    ("00", "SAGE-HLS", "arxiv", "2508.03558",
     "Khan et al., SAGE-HLS (source paper)"),
    ("01", "High-Level-Synthesis-Coussy-book", "none", "",
     "Springer 专著 (Coussy et al., 2010)，无公开 PDF"),
    ("02", "Are-We-There-Yet", "dspace",
     "https://trepo.tuni.fi/bitstream/handle/10024/214773/"
     "Are_We_There_Yet.pdf?sequence=1&isAllowed=y",
     "IEEE TCAD 2018；作者版取自 Tampere University 仓库"),
    ("03", "HLS-Introduction-to-Chip-and-System-Design", "none", "",
     "Springer 专著 (Gajski et al., 2012)，无公开 PDF"),
    ("04", "Predictive-Model-Based-HLS-DSE", "none", "",
     "DAC 2019, doi:10.1145/3316781.3317754 — ACM 闭源"),
    ("05", "Agile-AI-ML-Accelerator-End-to-End-Synthesis", "none", "",
     "ASAP 2021, doi:10.1109/asap52443.2021.00040 — IEEE 闭源"),
    ("06", "FPGA-HLS-Today", "none", "",
     "ACM TRETS 2022, doi:10.1145/3530775 — 标记 bronze OA，"
     "但 dl.acm.org PDF 实测 403"),
    ("07", "Vivado-HLS-for-Zynq-SoC", "none", "",
     "DCIS 2016, doi:10.1109/dcis.2016.7845376 — IEEE 闭源"),
    ("08", "SecHLS", "none", "",
     "ASP-DAC 2023 — 无 OA 版本"),
    ("09", "Language-Models-are-Few-Shot-Learners", "arxiv", "2005.14165", ""),
    ("10", "StructGPT", "arxiv", "2305.09645", ""),
    ("11", "GitHub-Copilot-Asset-or-Liability", "arxiv", "2206.15331", ""),
    ("12", "CodeGen", "arxiv", "2203.13474", ""),
    ("13", "VeriGen", "arxiv", "2308.00708", ""),
    ("14", "Chip-Chat", "arxiv", "2305.13243", ""),
    ("15", "ChipNeMo", "arxiv", "2311.00176", ""),
    ("16", "RTL-plus-plus", "arxiv", "2505.13479", ""),
    ("17", "CraftRTL", "arxiv", "2409.12993", ""),
    ("18", "DecoRTL", "arxiv", "2507.02226", ""),
    ("19", "AssertLLM", "arxiv", "2402.00386", ""),
    ("20", "LLM-Aided-Testbench-Generation-FSM", "arxiv", "2406.17132", ""),
    ("21", "LLM-IFT", "arxiv", "2504.07015", ""),
    ("22", "Self-HWDebug", "arxiv", "2405.12347", ""),
    ("23", "Fixing-Hardware-Security-Bugs-with-LLMs", "arxiv", "2302.01215",
     "TIFS 2024 期刊版的预印本"),
    ("24", "Evolutionary-LLMs-for-Hardware-Security", "arxiv", "2404.16651", ""),
    ("25", "Evaluating-LLMs-for-RTL-Generation-via-HLS", "arxiv", "2408.02793", ""),
    ("26", "HLSPilot", "arxiv", "2408.06810", ""),
    ("27", "Are-LLMs-Any-Good-for-HLS", "arxiv", "2408.10428", ""),
    ("28", "Automated-C-Cpp-Program-Repair-for-HLS", "arxiv", "2407.03889", ""),
    ("29", "SynthAI", "arxiv", "2405.16072", ""),
    ("30", "Optimizing-HLS-Designs-with-RAG-LLMs", "arxiv", "2410.07356", ""),
    ("31", "C2HLSC", "arxiv", "2406.09233", ""),
    ("32", "Agentic-HLS", "arxiv", "2412.01604", ""),
    ("33", "TimelyHLS", "arxiv", "2507.17962", ""),
    ("34", "Qwen2.5-Technical-Report", "arxiv", "2412.15115", ""),
    ("35", "VerilogEval", "arxiv", "2309.07544", ""),
    ("36", "Data-Is-All-You-Need", "arxiv", "2403.11202", ""),
    ("37", "ChatEDA", "arxiv", "2308.10204", ""),
    ("38", "RTLCoder", "arxiv", "2312.08617", ""),
    ("39", "OriGen", "arxiv", "2407.16237", ""),
    ("40", "BetterV", "arxiv", "2402.03375", ""),
    ("41", "AutoVCoder", "arxiv", "2407.18333", ""),
    ("42", "CodeV", "arxiv", "2407.10424", ""),
    ("43", "MAGE", "arxiv", "2412.07822", ""),
    ("44", "Think-on-Graph", "arxiv", "2307.07697", ""),
    ("45", "Talk-Like-a-Graph", "arxiv", "2310.04560", ""),
    ("46", "Let-Your-Graph-Do-the-Talking", "arxiv", "2402.05862", ""),
    ("47", "GraphLLM", "arxiv", "2310.05845", ""),
    ("48", "Tree-sitter", "none", "",
     "软件文档站点 https://tree-sitter.github.io/tree-sitter/ ，非论文"),
    ("49", "ScaleHLS", "arxiv", "2107.11673", ""),
    ("50", "CRAVE", "none", "",
     "SoC 2012, doi:10.1109/issoc.2012.6376356 — IEEE 闭源"),
    ("51", "LoRA", "arxiv", "2106.09685", ""),
    ("52", "ROUGE", "direct", "https://aclanthology.org/W04-1013.pdf",
     "ACL Anthology"),
]


def url_for(kind, target):
    if kind == "arxiv":
        return "https://arxiv.org/pdf/%s" % target
    return target


def looks_like_pdf(data):
    return data.startswith(b"%PDF") and len(data) >= MIN_BYTES


def strip_envelope(data):
    """DSpace bitstreams come back wrapped in a multipart/form-data envelope."""
    i = data.find(b"%PDF")
    return data[i:] if i > 0 else data


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last = None
    for attempt in range(len(BACKOFF) + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read(), None
        except (urllib.error.URLError, OSError) as exc:
            last = exc
            if attempt < len(BACKOFF):
                time.sleep(BACKOFF[attempt])
    return None, "%s: %s" % (type(last).__name__, last)


def main():
    results = []
    ok = failed = skipped = unavailable = 0

    for num, slug, kind, target, note in ENTRIES:
        label = "[%s] %s" % (num, slug)

        if kind == "none":
            unavailable += 1
            results.append((num, slug, "unavailable", "", note))
            print("%-8s UNAVAILABLE" % label)
            continue

        url = url_for(kind, target)
        dest = os.path.join(HERE, "%s-%s.pdf" % (num, slug))

        if os.path.exists(dest) and os.path.getsize(dest) >= MIN_BYTES:
            with open(dest, "rb") as fh:
                head = fh.read(4)
            if head == b"%PDF":
                skipped += 1
                results.append((num, slug, "ok (cached)", url, note))
                print("%-8s SKIP (already present)" % label)
                continue

        data, err = fetch(url)
        if kind == "arxiv":
            time.sleep(ARXIV_SLEEP)

        if data is None:
            failed += 1
            results.append((num, slug, "failed", url, err))
            print("%-8s FAIL  %s" % (label, err))
            continue

        if kind == "dspace":
            data = strip_envelope(data)

        if not looks_like_pdf(data):
            failed += 1
            reason = "响应不是有效 PDF (%d bytes, 开头 %r)" % (
                len(data), data[:16])
            results.append((num, slug, "failed", url, reason))
            print("%-8s FAIL  %s" % (label, reason))
            continue

        with open(dest, "wb") as fh:
            fh.write(data)
        ok += 1
        results.append((num, slug, "ok", url, note))
        print("%-8s OK    %d KB" % (label, len(data) // 1024))

    write_manifest(results)
    print("\n下载 %d 篇，跳过 %d 篇，失败 %d 篇，无公开版本 %d 篇。" %
          (ok, skipped, failed, unavailable))
    return 1 if failed else 0


def write_manifest(results):
    lines = [
        "# SAGE-HLS 参考文献下载清单",
        "",
        "源论文：Khan et al., *SAGE-HLS: Syntax-Aware AST-Guided LLM for "
        "High-Level Synthesis Code Generation*, arXiv:2508.03558 (2025)。",
        "编号与 `HLS代码智能体相关文献整理.docx` 中的 `[n]` 一致；`00` 为源论文本身。",
        "",
        "| # | 文献 | 状态 | 来源 / 说明 |",
        "| --- | --- | --- | --- |",
    ]
    for num, slug, status, url, note in results:
        detail = url or ""
        if note:
            detail = ("%s<br>%s" % (detail, note)) if detail else note
        lines.append("| %s | %s | %s | %s |" % (
            num, slug.replace("|", "\\|"), status, detail.replace("|", "\\|")))
    lines += [
        "",
        "重跑 `python fetch.py` 会跳过已下载的文件，只重试失败项。",
        "",
    ]
    path = os.path.join(HERE, "download-manifest.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
