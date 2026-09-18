#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sanity-check the downloaded PDFs: extract text and confirm each file is the
paper its filename claims.

Matching is whitespace-insensitive because PDF text operators split words
arbitrarily ("Data is all you need" may extract as "dataisallyouneed").
Standard library only.
"""
import glob
import re
import zlib

TOK = re.compile(rb"\(([^()\\]{1,200})\)")   # linear scan, no backtracking
STREAM = re.compile(rb"stream\r?\n")

# key -> distinctive title fragments (lowercase, spaces ignored when matching)
CHECKS = {
    "00": ["sage-hls"],
    "02": ["thereyet"],
    "09": ["few-shot"],
    "10": ["structgpt"],
    "11": ["copilot"],
    "12": ["codegen"],
    "13": ["verigen"],
    "14": ["chip-chat"],
    "15": ["chipnemo"],
    "16": ["rtl++"],
    "17": ["craftrtl"],
    "18": ["decortl"],
    "19": ["assertllm"],
    "20": ["testbench"],
    "21": ["informationflow"],
    "22": ["hwdebug"],
    "23": ["securitybug"],
    "24": ["hardwaresecurity"],
    "25": ["registertransfer", "high-levelsynthesis"],
    "26": ["hlspilot"],
    "27": ["high-levelsynthesis"],
    "28": ["programrepair"],
    "29": ["synthai"],
    "30": ["retrieval"],
    "31": ["c2hlsc"],
    "32": ["agentic"],
    "33": ["timelyhls"],
    "34": ["qwen"],
    "35": ["verilogeval"],
    "36": ["dataisallyouneed"],
    "37": ["chateda"],
    "38": ["rtlcoder"],
    "39": ["origen"],
    "40": ["betterv"],
    "41": ["autovcoder"],
    "42": ["codev"],
    "43": ["mage"],
    "44": ["think-on-graph"],
    "45": ["talklikeagraph"],
    "46": ["graphdothetalking"],
    "47": ["graphllm"],
    "49": ["scalehls"],
    "51": ["low-rank"],
    "52": ["rouge"],
}


def pdf_text(path, max_streams=400, budget=400000):
    """Concatenate text-showing operators from up to max_streams streams."""
    data = open(path, "rb").read()
    chunks = []
    used = 0
    for m in STREAM.finditer(data):
        start = m.end()
        end = data.find(b"endstream", start)
        if end < 0 or end - start > 3_000_000:
            continue
        try:
            raw = zlib.decompress(data[start:end])
        except Exception:
            continue
        if b"TJ" not in raw and b"Tj" not in raw:
            continue
        chunks.append(b"".join(TOK.findall(raw)))
        used += 1
        if used >= max_streams or sum(len(c) for c in chunks) > budget:
            break
    text = re.sub(rb"[^ -~]", b"", b"".join(chunks))
    return text.decode("ascii", "replace").lower()


def squash(s):
    return re.sub(r"\s+", "", s)


def main():
    review = []
    for num in sorted(CHECKS):
        found = glob.glob(num + "-*.pdf")
        if not found:
            print("MISS %s-*.pdf not found" % num)
            review.append(num)
            continue
        name = found[0]
        text = squash(pdf_text(name))
        hits = {k: (squash(k) in text) for k in CHECKS[num]}
        if any(hits.values()):
            print("OK   %s" % name)
        else:
            print("CHK  %-52s %s" % (name, hits))
            print("      head: %s" % text[:200])
            review.append(name)
    print("\nverified %d / %d; needs review: %s" %
          (len(CHECKS) - len(review), len(CHECKS), review or "none"))


if __name__ == "__main__":
    main()
