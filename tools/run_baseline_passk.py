"""Run independent, no-retry baseline samples for a fixed HLS task list."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from serve.baseline_entry import run

DONE = {"generated", "failed"}


def write(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def record_from_disk(task, sample, dest):
    path = dest / "result.json"
    if not path.is_file():
        return None
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("status") not in DONE:
        return None
    return {"task_id": task, "sample": sample, "exit_code": 0 if result["status"] == "generated" else 1, "result": result}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    p.add_argument("--task-list", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--samples", type=int, default=5)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--resume", action="store_true")
    a = p.parse_args()
    data, task_list, out = Path(a.dataset).resolve(), Path(a.task_list).resolve(), Path(a.output).resolve()
    if not out.is_relative_to(ROOT / "output") or a.samples < 1 or a.workers < 1:
        raise ValueError("output must be a new or resumed directory under output/, with positive samples/workers")
    if out.exists() != a.resume:
        raise ValueError("new runs require a missing output directory; resume requires an existing one")
    tasks = json.loads(task_list.read_text(encoding="utf-8"))["task_order"]
    if len(tasks) != len(set(tasks)):
        raise ValueError("task list contains duplicates")
    config = json.loads((ROOT / "serve/runtime.json").read_text(encoding="utf-8"))
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    if a.resume:
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("config_sha256") != config_hash or manifest.get("tasks") != tasks or manifest.get("samples") != a.samples:
            raise ValueError("resume config, task list or sample count does not match the existing run")
        by_key = {(r["task_id"], r["sample"]): r for r in manifest.get("results", [])}
        cleared = []
        for task in tasks:
            for sample in range(1, a.samples + 1):
                dest = out / "tasks" / task / f"sample_{sample}"
                disk = record_from_disk(task, sample, dest)
                if disk:
                    by_key[(task, sample)] = disk
                    continue
                if dest.exists():
                    shutil.rmtree(dest)
                    cleared.append({"task_id": task, "sample": sample})
                    by_key.pop((task, sample), None)
        manifest["results"] = [by_key[key] for key in sorted(by_key)]
        manifest.setdefault("worker_changes", []).append({
            "at": datetime.now(timezone.utc).isoformat(),
            "from": manifest.get("workers"),
            "to": a.workers,
            "completed_before": len(manifest["results"]),
            "cleared_incomplete": cleared,
        })
    else:
        out.mkdir(parents=True)
        manifest = {"status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
                    "mode": "baseline_only_problem_raw_single_request_no_retry", "task_count": len(tasks),
                    "samples": a.samples, "workers": a.workers, "config": config, "config_sha256": config_hash,
                    "tasks": tasks, "results": []}
    manifest.update(status="running", workers=a.workers, config=config, config_sha256=config_hash)
    manifest.pop("finished_at", None)
    write(out / "manifest.json", manifest)
    done = {(r["task_id"], r["sample"]) for r in manifest["results"]}
    jobs = []
    for task in tasks:
        problem = data / task / "kernel_description.md"
        if not problem.is_file():
            raise FileNotFoundError(problem)
        for sample in range(1, a.samples + 1):
            if (task, sample) not in done:
                jobs.append((task, sample, problem, out / "tasks" / task / f"sample_{sample}"))
    def one(job):
        task, sample, problem, dest = job
        try:
            code, result = run(problem, dest, config, f"{task}/sample_{sample}")
            return {"task_id": task, "sample": sample, "exit_code": code, "result": result}
        except Exception as e:
            return {"task_id": task, "sample": sample, "exit_code": 1, "error": type(e).__name__ + ": " + str(e)}
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for future in as_completed([pool.submit(one, j) for j in jobs]):
            manifest["results"].append(future.result())
            write(out / "manifest.json", manifest)
    manifest["status"] = "completed"
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    manifest["generated"] = sum(r["exit_code"] == 0 for r in manifest["results"])
    write(out / "manifest.json", manifest)


if __name__ == "__main__": main()
