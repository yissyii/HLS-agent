"""Retry network/HTTP failures in an existing pass@k run until none remain."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from serve.baseline_entry import run

NETWORK = {"api_network_or_timeout", "api_http_error"}
DATA = ROOT.parent / "hls-overlap-audit" / "hls-eval" / "hls_eval_data"


def write(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def network(record):
    return (record.get("result") or {}).get("category") in NETWORK


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run", required=True)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--max-rounds", type=int, default=8)
    a = p.parse_args()
    out = Path(a.run).resolve()
    if not out.is_relative_to(ROOT / "output"):
        raise ValueError("run directory must be under output/")
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    config = manifest["config"]
    data = DATA
    history = manifest.setdefault("network_recovery", [])
    for round_id in range(1, a.max_rounds + 1):
        by_key = {(r["task_id"], r["sample"]): r for r in manifest["results"]}
        jobs = []
        for (task, sample), record in list(by_key.items()):
            if not network(record):
                continue
            dest = out / "tasks" / task / f"sample_{sample}"
            if dest.exists():
                shutil.rmtree(dest)
            jobs.append((task, sample, data / task / "kernel_description.md", dest))
        entry = {"round": round_id, "at": datetime.now(timezone.utc).isoformat(), "targets": len(jobs), "results": []}
        if not jobs:
            entry["status"] = "nothing_to_retry"
            history.append(entry)
            write(out / "manifest.json", manifest)
            break
        def one(job):
            task, sample, problem, dest = job
            try:
                code, result = run(problem, dest, config, f"{task}/sample_{sample}/network_recovery_{round_id}")
                return {"task_id": task, "sample": sample, "exit_code": code, "result": result}
            except Exception as error:
                return {"task_id": task, "sample": sample, "exit_code": 1, "error": type(error).__name__ + ": " + str(error)}
        with ThreadPoolExecutor(max_workers=a.workers) as pool:
            for future in as_completed([pool.submit(one, job) for job in jobs]):
                record = future.result()
                by_key[(record["task_id"], record["sample"])] = record
                entry["results"].append({"task_id": record["task_id"], "sample": record["sample"],
                                         "exit_code": record.get("exit_code"),
                                         "category": (record.get("result") or {}).get("category"),
                                         "status": (record.get("result") or {}).get("status")})
                manifest["results"] = [by_key[key] for key in sorted(by_key)]
                write(out / "manifest.json", manifest)
        remaining = sum(1 for record in by_key.values() if network(record))
        generated = sum(1 for record in by_key.values() if record.get("exit_code") == 0)
        entry.update(status="completed", remaining_network=remaining, generated=generated)
        history.append(entry)
        manifest["results"] = [by_key[key] for key in sorted(by_key)]
        manifest["generated"] = generated
        write(out / "manifest.json", manifest)
        if remaining == 0:
            break
    else:
        raise SystemExit("network failures remain after max recovery rounds")
    manifest["status"] = "completed"
    manifest["network_recovery_finished_at"] = datetime.now(timezone.utc).isoformat()
    write(out / "manifest.json", manifest)


if __name__ == "__main__":
    main()
