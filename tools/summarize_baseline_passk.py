"""Summarize a running or finished baseline pass@k generation manifest."""
import argparse
from collections import Counter
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    slots = manifest["task_count"] * manifest["samples"]
    results = manifest.get("results", [])
    status = Counter()
    extraction = Counter()
    category = Counter()
    for record in results:
        result = record.get("result") or {}
        status[result.get("status") or record.get("error", "missing")] += 1
        if result.get("source_extraction"):
            extraction[result["source_extraction"]] += 1
        if result.get("category"):
            category[result["category"]] += 1
    report = {
        "run_status": manifest.get("status"),
        "completed": len(results),
        "slots": slots,
        "generated_candidates": sum(1 for record in results if record.get("exit_code") == 0),
        "status": dict(status),
        "source_extraction": dict(extraction),
        "failure_category": dict(category),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
