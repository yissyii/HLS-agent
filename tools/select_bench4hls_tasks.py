"""Classify Bench4HLS tasks, drop trivially simple ones, and stratified-sample.

Categories (keyword rules over the natural-language prompt, overridable by the
explicit ID ranges the benchmark itself is ordered by):

    kernel        -- DSP / numeric / crypto kernels (FIR, FFT, matrix, SHA, ...)
    sequential    -- registers, counters, FSMs, serial protocols, cellular
                     automata, branch predictors, timers
    combinational -- pure logic (gates, adders, muxes, decoders, K-maps)

"Too simple" tasks (trivial gates / basic register primitives) are excluded by
structural criteria. The remaining tasks are sampled per category to hit the
requested total, kernels and the most complex tasks first.

Usage:
  python -B tools/select_bench4hls_tasks.py --total 50 --seed 0 [--print-all]
"""
import argparse
import csv
import io
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "Bench4HLS"
PROCESSED = ROOT / "data" / "processed" / "bench4hls"

# Prob151-170 are the benchmark's DSP / numeric / crypto kernels (FIR, FFT,
# matrix, convolution, SHA, ...). Prob001-150 are VerilogEval-style RTL logic.
KERNEL_MIN = 151
SEQUENTIAL_TERMS = [
    "flip-flop", "flip flop", "register", "counter", "shift register",
    "state machine", "fsm", "sequence", "detect", "serial", "protocol",
    "cellular automaton", "timer", "branch predictor", "memory", "moore",
    "mealy", "reset", "clock", "hdlc", "ps/2", "ps2", "scancode", "lemmings",
    "rule 90", "rule 110", "shift", "rotator", "lfsr", "feedback shift",
    "edge", "state", "divider", "thermostat", "reserv", "keyboard", "mouse",
]


def features(name):
    ref = (RAW / "benchmark" / "reference_design" / f"{name}_ref.cpp").read_text(encoding="utf-8")
    prompt = (RAW / "benchmark" / "prompts" / f"{name}_prompt.txt").read_text(encoding="utf-8")
    loc = len([l for l in ref.splitlines() if l.strip()])
    loops = len(re.findall(r"\b(?:for|while)\s*\(", ref))
    arrays = len(re.findall(r"\[[0-9A-Z_]+\]", ref))
    report = {}
    for row in csv.DictReader(io.StringIO((RAW / "benchmark" / "report_ref.csv").read_text(encoding="utf-8-sig"))):
        if row["Design"] == f"{name}_ref":
            report = row
            break
    latency = float(report.get("Latency(ns)", "0") or 0)
    return dict(name=name, loc=loc, loops=loops, arrays=arrays, latency=latency, prompt=prompt)


def classify(feat):
    if int(feat["name"].replace("Prob", "")) >= KERNEL_MIN:
        return "kernel"
    text = feat["prompt"].lower()
    if any(t in text for t in SEQUENTIAL_TERMS):
        return "sequential"
    return "combinational"


def is_too_simple(feat, category):
    # Trivial combinational logic (gates / small mux / adder / K-map).
    if category == "combinational" and feat["loc"] <= 15:
        return True
    # Trivial register/clock primitive (single DFF / simple counter).
    if category == "sequential" and feat["loc"] <= 12 and feat["loops"] == 0 and feat["arrays"] == 0:
        return True
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--total", type=int, default=50)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--print-all", action="store_true")
    args = parser.parse_args()

    tasks = [features(p.stem.replace("_ref", ""))
             for p in sorted((RAW / "benchmark" / "reference_design").glob("Prob*_ref.cpp"))]
    for feat in tasks:
        feat["category"] = classify(feat)
        feat["too_simple"] = is_too_simple(feat, feat["category"])

    kept = [t for t in tasks if not t["too_simple"]]
    # Rank within category: more complex first (loops, arrays, latency, then LOC).
    for t in kept:
        t["_score"] = (t["loops"] * 5 + t["arrays"] * 3 + (1 if t["latency"] > 0 else 0) * 10 + t["loc"])

    selected = []
    for category in ("kernel", "sequential", "combinational"):
        pool = sorted([t for t in kept if t["category"] == category],
                      key=lambda t: (-t["_score"], t["name"]))
        selected.extend(pool)

    # Cap to requested total while keeping every kernel.
    kernels = [t for t in selected if t["category"] == "kernel"]
    others = [t for t in selected if t["category"] != "kernel"]
    budget = args.total - len(kernels)
    if budget < 0:
        kernels = kernels[: args.total]
        selected = kernels
    else:
        selected = kernels + others[: budget]
    selected = sorted(selected, key=lambda t: t["name"])

    from collections import Counter
    counts = Counter(t["category"] for t in selected)
    dropped = [t["name"] for t in tasks if t["too_simple"]]

    print(f"total={len(tasks)} kept={len(kept)} too_simple={len(dropped)} selected={len(selected)}")
    print("selected by category:", dict(counts))
    print("selected ids:", " ".join(t["name"] for t in selected))

    out = PROCESSED / "selection.json"
    out.write_text(json.dumps({
        "total": args.total, "seed": args.seed,
        "categories": {
            "kernel": [t["name"] for t in selected if t["category"] == "kernel"],
            "sequential": [t["name"] for t in selected if t["category"] == "sequential"],
            "combinational": [t["name"] for t in selected if t["category"] == "combinational"],
        },
        "all_tasks": [{ "name": t["name"], "category": t["category"],
                        "too_simple": t["too_simple"], "loc": t["loc"],
                        "loops": t["loops"], "arrays": t["arrays"],
                        "latency": t["latency"] } for t in tasks],
        "dropped_too_simple": dropped,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}")

    if args.print_all:
        for t in tasks:
            flag = "DROP" if t["too_simple"] else ("KEEP" if t in selected else "skip")
            print(f'{t["name"]} {t["category"]:<13} loc={t["loc"]:3d} loop={t["loops"]} arr={t["arrays"]:2d} lat={t["latency"]:>6} {flag}')


if __name__ == "__main__":
    main()
