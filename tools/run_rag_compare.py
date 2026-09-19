"""Compare RAG repair conditions (off / bm25 / hybrid) on Bench4HLS.

Two-phase, fixed first draft:
  Phase 1 (sequential): run the "off" condition, which generates the first
    draft (temperature pinned to 0, deterministic under non-concurrent
    scheduling) and captures candidates/000/response.txt.
  Phase 2 (parallel): run "bm25" and "hybrid" with --initial-source pointing at
    the off draft, so all three conditions start from the identical first draft
    and differ only in the repair-stage retrieval mode.

Usage:
  python -B tools/run_rag_compare.py \\
      --config serve/runtime.rag-eval.json \\
      --rag-runtime rag/runtime.local.json --workers 4
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

# (condition, policy path or None, rag-runtime path or None)
CONDITIONS = [
    ("off", None, None),
    ("bm25", "agent/config/policy.rag-bm25.json", "rag/runtime.local.json"),
    ("hybrid", "agent/config/policy.rag-hybrid.json", "rag/runtime.local.json"),
]


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
        "rag_enabled": receipt.get("rag_enabled"),
        "rag_history": receipt.get("rag_history"),
    }


def _entry(task_dir, out_dir, config, condition, policy, rag_runtime, initial_source=None):
    cmd = [sys.executable, "-B", "-m", "agent.interface.entry",
           str(task_dir / "problem.txt"), str(out_dir), "--config", config,
           "--task-manifest", str(task_dir / "task.json")]
    if policy:
        cmd += ["--policy", str(ROOT / policy), "--rag-runtime", rag_runtime]
    if initial_source and Path(initial_source).is_file():
        cmd += ["--initial-source", str(initial_source)]
    r = _run(cmd)
    receipt = _last_json(r.stdout)
    entry = {"task": task_dir.name, "method": condition, "stage": "validated"}
    entry.update(_metrics(receipt))
    entry["ok"] = bool(entry.get("overall"))
    if not entry.get("checks"):
        entry["detail"] = (r.stdout + r.stderr)[-800:]
    history = entry.get("rag_history") or []
    entry["rag_injected_ids"] = [h.get("injected_ids") for h in history]
    entry["rag_injected_bytes"] = [h.get("injected_bytes") for h in history]
    return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="Number of tasks; 0 = all")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--config", default="serve/runtime.rag-eval.json")
    parser.add_argument("--rag-runtime", default="rag/runtime.local.json")
    parser.add_argument("--task", help="Run a single Prob id (e.g. Prob001)")
    parser.add_argument("--selection", help="Selection JSON from select_bench4hls_tasks.py")
    parser.add_argument("--dataset", default=str(DATASET), help="Any directory with compatible task folders")
    args = parser.parse_args()
    return development_evaluation(args, _evaluate)


def _discover(args):
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
    return task_dirs


def _evaluate(args):
    task_dirs = _discover(args)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    batch_dir = output_path(ROOT / "output" / ("rag_compare_" + stamp))
    batch_dir.mkdir(parents=True)
    summary = {"batch_id": "rag_compare_" + stamp, "config": args.config,
               "rag_runtime": args.rag_runtime, "workers": args.workers,
               "two_phase_fixed_draft": True,
               "conditions": [{"name": name, "policy": policy, "rag_runtime": rt}
                              for name, policy, rt in CONDITIONS],
               "task_count": len(task_dirs), "status": "running", "results": [],
               "started_at": datetime.now(timezone.utc).isoformat()}
    summary_file = batch_dir / "summary.json"

    def flush():
        summary_file.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    def note(entry):
        summary["results"].append(entry)
        flush()
        print(json.dumps({"task": entry.get("task"), "method": entry.get("method"),
                          "ok": entry.get("ok"), "checks": entry.get("checks"),
                          "status": entry.get("status"), "stop_reason": entry.get("stop_reason"),
                          "api_requests": entry.get("api_requests"),
                          "elapsed_seconds": entry.get("elapsed_seconds"),
                          "done": len(summary["results"]), "total": len(task_dirs) * 3},
                         ensure_ascii=False), flush=True)

    flush()
    started = time.monotonic()

    # Phase 1: off condition, sequential, generates the canonical first draft.
    drafts = {}
    for d in task_dirs:
        out_dir = batch_dir / d.name / "off"
        try:
            entry = _entry(d, out_dir, args.config, "off", None, args.rag_runtime)
        except Exception as error:
            entry = {"task": d.name, "method": "off", "stage": "runner_error",
                     "ok": False, "reason": type(error).__name__, "message": str(error)}
        draft = out_dir / "candidates" / "000" / "response.txt"
        drafts[d.name] = draft if draft.is_file() else None
        note(entry)

    # Phase 2: bm25 + hybrid, parallel, reuse the fixed first draft.
    def work():
        for d in task_dirs:
            for name, policy, rt in CONDITIONS[1:]:
                yield d, name, policy, rt

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_entry, d, batch_dir / d.name / name, args.config,
                               name, policy, rt, drafts[d.name]): (d.name, name)
                   for d, name, policy, rt in work()}
        for future in as_completed(futures):
            task, cond = futures[future]
            try:
                result = future.result()
            except Exception as error:  # includes subprocess.TimeoutExpired
                result = {"task": task, "method": cond, "stage": "runner_error",
                          "ok": False, "reason": type(error).__name__, "message": str(error)}
            note(result)

    summary["status"] = "completed"
    summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()

    by_cond = {}
    for name, _, _ in CONDITIONS:
        rows = [r for r in summary["results"] if r.get("method") == name]
        n = len(rows)
        def rate(key):
            return sum(bool(r.get(key)) for r in rows) / n if n else 0.0
        by_cond[name] = {
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
        reqs = [x for x in by_cond[name]["api_requests"] if isinstance(x, (int, float))]
        elap = [x for x in by_cond[name]["elapsed_seconds"] if isinstance(x, (int, float))]
        by_cond[name]["mean_api_requests"] = round(sum(reqs) / len(reqs), 2) if reqs else None
        by_cond[name]["mean_elapsed_seconds"] = round(sum(elap) / len(elap), 2) if elap else None

    summary["comparison"] = by_cond
    flush()

    print("\n===== Bench4HLS RAG comparison (off / bm25 / hybrid) =====")
    print(f"tasks={len(task_dirs)} workers={args.workers} summary={summary_file.relative_to(ROOT)}")
    print(f"{'metric':<14}" + "".join(f"{name:>12}" for name, _, _ in CONDITIONS))
    for key, label in (("compile", "compile"), ("run", "csim/run"),
                       ("synthesize", "synthesis"), ("overall", "overall")):
        line = f"{label:<14}"
        for name, _, _ in CONDITIONS:
            b = by_cond[name]
            line += f"{b[key]:>8}/{b['tasks']:<3}"
        print(line)
    line = f"{'mean_api_req':<14}"
    for name, _, _ in CONDITIONS:
        line += f"{str(by_cond[name]['mean_api_requests']):>12}"
    print(line)
    line = f"{'mean_elapsed_s':<14}"
    for name, _, _ in CONDITIONS:
        line += f"{str(by_cond[name]['mean_elapsed_seconds']):>12}"
    print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
