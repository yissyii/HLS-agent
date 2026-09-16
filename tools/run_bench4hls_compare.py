"""Compare the strict baseline and the agent entry on Bench4HLS tasks.

Baseline = serve/baseline_entry.py (one generation, no validation) followed by a
validation-only ``evaluation.single_task --source`` pass.
Agent    = agent.interface.entry --task-manifest (generation + validation + up
to the frozen policy's ``max_repairs`` repairs).

Both are run per task in subprocesses for isolation, scheduled on a thread pool
(``--workers``, default 4). Results aggregate compile / csim-run / synthesis
pass rates plus request counts and elapsed time, so the baseline -> agent delta
is visible.

Usage:
  python -B tools/run_bench4hls_compare.py --limit 20 --workers 4
  python -B tools/run_bench4hls_compare.py --config serve/runtime.local.json
"""
import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evaluation.lifecycle import evaluate as development_evaluation, output_path
DATASET = ROOT / "data" / "processed" / "bench4hls"

# A single harness run is bounded by runtime.json's total_timeout_seconds (600);
# leave headroom above that for the subprocess wrapper.
PER_TASK_TIMEOUT = 720


def _run(cmd, timeout=PER_TASK_TIMEOUT):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _last_json(output):
    for line in reversed([l for l in output.splitlines() if l.strip()]):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return {}


def _metrics(receipt):
    checks = receipt.get("checks") or {}
    return {
        "checks": checks,
        "compile": checks.get("compile") == "passed",
        "run": checks.get("run") == "passed",
        "synthesize": checks.get("synthesize") == "passed",
        "overall": checks.get("run") == "passed" and checks.get("synthesize") == "passed",
        "validation_status": receipt.get("validation_status"),
        "status": receipt.get("status"),
        "stop_reason": receipt.get("stop_reason"),
        "api_requests": receipt.get("api_requests_recorded"),
        "elapsed_seconds": receipt.get("elapsed_seconds"),
    }


def _result_json(stdout_wrapper):
    # evaluation.single_task prints {"result": "output/runs/<id>/result.json"}.
    path = stdout_wrapper.get("result")
    if not path:
        return {}
    try:
        return json.loads((ROOT / path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def eval_baseline(task_dir, batch_dir, config, cpu_only):
    name = task_dir.name
    gen_out = batch_dir / name / "baseline_gen"
    r = _run([sys.executable, "-B", "serve/baseline_entry.py",
              str(task_dir / "problem.txt"), str(gen_out), "--config", config])
    gen = _last_json(r.stdout)
    entry = {"task": name, "method": "baseline", "generation": gen.get("status"),
             "generation_category": gen.get("category")}
    if r.returncode != 0 or gen.get("status") != "generated" or not (gen_out / "candidate.cpp").is_file():
        entry.update(stage="generation", ok=False,
                     reason=gen.get("category") or "generation_failed",
                     message=gen.get("message", ""),
                     detail=(r.stdout + r.stderr)[-800:])
        return entry
    r2 = _run([sys.executable, "-B", "-m", "evaluation.single_task",
               str(task_dir / "task.json"), "--source", str(gen_out / "candidate.cpp"),
               "--config", config] + (["--cpu-only"] if cpu_only else []))
    receipt = _result_json(_last_json(r2.stdout))
    entry.update(_metrics(receipt))
    entry["api_requests"] = gen.get("generation_requests", 0)
    entry["stage"] = "validated"
    entry["ok"] = bool(entry.get("overall"))
    if not entry.get("checks"):
        entry["detail"] = (r2.stdout + r2.stderr)[-800:]
    return entry


def eval_agent(task_dir, batch_dir, config, cpu_only):
    name = task_dir.name
    out = batch_dir / name / "agent"
    r = _run([sys.executable, "-B", "-m", "agent.interface.entry",
              str(task_dir / "problem.txt"), str(out), "--config", config,
              "--task-manifest", str(task_dir / "task.json")] +
             (["--cpu-only"] if cpu_only else []))
    receipt = _last_json(r.stdout)
    entry = {"task": name, "method": "agent", "stage": "validated"}
    entry.update(_metrics(receipt))
    entry["ok"] = bool(entry.get("overall"))
    if not entry.get("checks"):
        entry["detail"] = (r.stdout + r.stderr)[-800:]
    return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="Number of tasks; 0 = all")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--config", default="serve/runtime.local.json")
    parser.add_argument("--cpu-only", action="store_true")
    parser.add_argument("--task", help="Run a single Prob id (e.g. Prob001)")
    parser.add_argument("--selection", help="Selection JSON from select_bench4hls_tasks.py")
    parser.add_argument("--dataset", default=str(DATASET), help="Any directory with compatible task folders")
    args = parser.parse_args()
    return development_evaluation(args, _evaluate)


def _evaluate(args):
    dataset = Path(args.dataset).resolve()

    task_dirs = sorted(p.parent for p in dataset.glob('*/task.json'))
    if args.task:
        task_dirs = [d for d in task_dirs if d.name == args.task]
        if not task_dirs:
            raise SystemExit(f"Task not found: {args.task}")
    elif args.selection or (dataset / "selection.json").is_file():
        selection_path = dataset / (args.selection or "selection.json")
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        ids = [n for names in selection["categories"].values() for n in names]
        task_dirs = [d for d in task_dirs if d.name in set(ids)]
        if args.limit:
            task_dirs = task_dirs[: args.limit]
    elif args.limit:
        task_dirs = task_dirs[: args.limit]
    if not task_dirs:
        raise SystemExit("No tasks to run")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    batch_dir = output_path(ROOT / "output" / ("compare_" + stamp))
    batch_dir.mkdir(parents=True)
    summary = {"batch_id": "compare_" + stamp, "config": args.config,
               "workers": args.workers, "cpu_only": args.cpu_only,
               "task_count": len(task_dirs), "status": "running", "results": [],
               "started_at": datetime.now(timezone.utc).isoformat()}
    summary_file = batch_dir / "summary.json"
    summary_file.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    def work():
        for d in task_dirs:
            for fn in (eval_baseline, eval_agent):
                yield fn, d

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fn, d, batch_dir, args.config, args.cpu_only): (d.name, fn.__name__)
                   for fn, d in work()}
        for future in as_completed(futures):
            name, fn = futures[future]
            try:
                result = future.result()
            except Exception as error:  # includes subprocess.TimeoutExpired
                result = {"task": name, "method": fn, "stage": "runner_error",
                          "ok": False, "reason": type(error).__name__, "message": str(error)}
            summary["results"].append(result)
            summary_file.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"task": result.get("task"), "method": result.get("method"),
                              "ok": result.get("ok"), "checks": result.get("checks"),
                              "status": result.get("status"), "stop_reason": result.get("stop_reason"),
                              "api_requests": result.get("api_requests"),
                              "elapsed_seconds": result.get("elapsed_seconds"),
                              "done": len(summary["results"]), "total": len(futures)},
                             ensure_ascii=False), flush=True)

    summary["status"] = "completed"
    summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()

    by_method = {}
    for method in ("baseline", "agent"):
        rows = [r for r in summary["results"] if r.get("method") == method]
        n = len(rows)
        def rate(key):
            return sum(bool(r.get(key)) for r in rows) / n if n else 0.0
        by_method[method] = {
            "tasks": n,
            "compile": sum(bool(r.get("compile")) for r in rows),
            "run": sum(bool(r.get("run")) for r in rows),
            "synthesize": sum(bool(r.get("synthesize")) for r in rows),
            "overall": sum(bool(r.get("overall")) for r in rows),
            "compile_rate": rate("compile"),
            "run_rate": rate("run"),
            "synthesize_rate": rate("synthesize"),
            "overall_rate": rate("overall"),
            "api_requests": [r.get("api_requests") for r in rows],
            "elapsed_seconds": [r.get("elapsed_seconds") for r in rows],
        }
        reqs = [x for x in by_method[method]["api_requests"] if isinstance(x, (int, float))]
        elap = [x for x in by_method[method]["elapsed_seconds"] if isinstance(x, (int, float))]
        by_method[method]["mean_api_requests"] = round(sum(reqs) / len(reqs), 2) if reqs else None
        by_method[method]["mean_elapsed_seconds"] = round(sum(elap) / len(elap), 2) if elap else None

    summary["comparison"] = by_method
    summary_file.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print("\n===== Bench4HLS baseline vs agent =====")
    print(f"tasks={len(task_dirs)} workers={args.workers} summary={summary_file.relative_to(ROOT)}")
    print(f"{'metric':<14}{'baseline':>12}{'agent':>12}{'delta':>12}")
    b, a = by_method["baseline"], by_method["agent"]
    for key, label in (("compile", "compile"), ("run", "csim/run"),
                       ("synthesize", "synthesis"), ("overall", "overall")):
        bc, ac = b[key], a[key]
        print(f"{label:<14}{bc:>8}/{b['tasks']:<3}{ac:>8}/{a['tasks']:<3}{ac - bc:>+12}")
    print(f"{'mean_api_req':<14}{str(b['mean_api_requests']):>12}{str(a['mean_api_requests']):>12}")
    print(f"{'mean_elapsed_s':<14}{str(b['mean_elapsed_seconds']):>12}{str(a['mean_elapsed_seconds']):>12}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
