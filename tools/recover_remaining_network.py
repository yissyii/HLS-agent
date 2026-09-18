"""Retry only unresolved infrastructure-failed baseline samples."""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from serve.baseline_entry import run

BASE = ROOT / "output/frozen_baseline_pass5_20260915T214330"
READY = ROOT / "output/baseline_pass5_validation_ready_20260915/manifest.json"
OUT = ROOT / "output/baseline_pass5_network_recovery_round3_20260916"
DATA = ROOT.parent / "hls-overlap-audit/hls-eval/hls_eval_data"


def write(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def good(record):
    return record.get("exit_code") == 0 and (record.get("result") or {}).get("source") == "candidate.cpp"


def main():
    base = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))
    ready = json.loads(READY.read_text(encoding="utf-8"))
    existing = {(r["task_id"], r["sample"]) for r in ready["records"]}
    targets = [r for r in base["results"] if (r.get("result") or {}).get("category") in {"api_network_or_timeout", "api_http_error"} and (r["task_id"], r["sample"]) not in existing]
    if OUT.exists():
        raise FileExistsError(OUT)
    OUT.mkdir(parents=True)
    manifest = {"status": "running", "mode": "network_http_only_recovery_same_config", "started_at": datetime.now(timezone.utc).isoformat(), "source_manifest": str(BASE / "manifest.json"), "source_config_sha256": base["config_sha256"], "targets": [{"task_id": r["task_id"], "sample": r["sample"]} for r in targets], "results": []}
    write(OUT / "manifest.json", manifest)
    def one(record):
        task, sample = record["task_id"], record["sample"]
        try:
            code, result = run(DATA / task / "kernel_description.md", OUT / "tasks" / task / f"sample_{sample}", base["config"], f"{task}/sample_{sample}/network_recovery_round3")
            return {"task_id": task, "sample": sample, "exit_code": code, "result": result}
        except Exception as error:
            return {"task_id": task, "sample": sample, "exit_code": 1, "error": f"{type(error).__name__}: {error}"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(one, target) for target in targets]):
            manifest["results"].append(future.result())
            write(OUT / "manifest.json", manifest)
    manifest.update(status="completed", finished_at=datetime.now(timezone.utc).isoformat(), generated=sum(good(r) for r in manifest["results"]))
    write(OUT / "manifest.json", manifest)


if __name__ == "__main__":
    main()
