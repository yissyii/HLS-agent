"""Compare repair-only RAG conditions from one independently generated valid draft.

All conditions use the same scheduling, repair budget, frozen inputs and draft.
Generation/initial validation cost is recorded separately. Invalid drafts are
reported consistently and never regenerated in a comparison condition.
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
from agent.core.policy import load_policy
from agent.context.retrieval import load_runtime, Retrieval
from serve.inference import load_config, write_json
from rag.common import file_sha256
DATASET = ROOT / "data" / "processed" / "bench4hls"

# A single harness run is bounded by runtime.json's total_timeout_seconds (600);
# leave headroom above that for the subprocess wrapper.
PER_TASK_TIMEOUT = 720

CONDITIONS = ("off", "bm25", "hybrid")


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


def _entry(task_dir, out_dir, config, condition, policy, rag_runtime, initial_source=None, timeout=PER_TASK_TIMEOUT):
    cmd = [sys.executable, "-B", "-m", "agent.interface.entry",
           str(task_dir / "problem.txt"), str(out_dir), "--config", config,
           "--task-manifest", str(task_dir / "task.json")]
    cmd += ["--policy", str(policy), "--rag-runtime", str(rag_runtime)]
    if condition != "draft":
        if initial_source is None or not Path(initial_source).is_file():
            raise ValueError("Comparison requires a valid frozen first draft")
        cmd += ["--initial-source", str(initial_source)]
    r = _run(cmd, timeout=timeout)
    receipt_path = Path(out_dir) / "result.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else _last_json(r.stdout)
    entry = {"task": task_dir.name, "method": condition, "stage": "validated"}
    entry.update(_metrics(receipt))
    entry["ok"] = bool(entry.get("overall"))
    entry["exit_code"] = r.returncode
    if initial_source is not None:
        initial_hash = file_sha256(Path(initial_source))
        candidates = receipt.get("candidates", [])
        actual = next((c.get("source_sha256") for c in candidates if c.get("candidate_id") == 0), None)
        entry["initial_source_sha256"] = initial_hash
        entry["initial_source_verified"] = actual == initial_hash
        if actual is not None and actual != initial_hash:
            raise ValueError("Comparison candidate differs from frozen draft")
    if not entry.get("checks"):
        entry["detail"] = (r.stdout + r.stderr)[-800:]
    history = entry.get("rag_history") or []
    entry["rag_injected_ids"] = [h.get("injected_ids") for h in history]
    entry["rag_injected_bytes"] = [h.get("injected_bytes") for h in history]
    return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="Number of tasks; 0 = all")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--policy", default="agent/config/policy.rag-hybrid.json",
                        help="Common policy; only RAG enable/mode differ between conditions")
    parser.add_argument("--config", default="serve/runtime.rag-eval.json")
    parser.add_argument("--rag-runtime", default="rag/runtime.local.json")
    parser.add_argument("--task", help="Run a single Prob id (e.g. Prob001)")
    parser.add_argument("--selection", help="Selection JSON from select_bench4hls_tasks.py")
    parser.add_argument("--dataset", default=str(DATASET), help="Any directory with compatible task folders")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    args.rag_compare = True  # Freeze hybrid artifacts even if the base policy disables RAG.
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


def _valid_draft(directory):
    """Only an extracted candidate from a successful generation is reusable."""
    directory = Path(directory)
    receipt_path = directory / "result.json"
    source = directory / "candidates/000/candidate.cpp"
    if not receipt_path.is_file() or not source.is_file():
        return None
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    generation = receipt.get("stages", {}).get("generation", {})
    if generation.get("status") != "passed":
        return None
    if generation.get("finish_reason", "stop") != "stop":
        return None
    expected = next((c.get("source_sha256") for c in receipt.get("candidates", [])
                     if c.get("candidate_id") == 0), None)
    return source if expected == file_sha256(source) else None


def summarize(rows, drafts):
    conditions = {}
    failed_drafts = {r["task"] for r in drafts if r.get("valid_source") and
                     r.get("stop_reason") == "repair_budget_exhausted"}
    for name in CONDITIONS:
        values = [r for r in rows if r["method"] == name]
        aggregate = {"tasks": len(values)}
        for key in ("compile", "run", "synthesize", "overall"):
            aggregate[key] = sum(bool(r.get(key)) for r in values)
            aggregate[key + "_rate"] = aggregate[key] / len(values) if values else None
        for key in ("api_requests", "elapsed_seconds"):
            numbers = [r[key] for r in values if isinstance(r.get(key), (float, int))]
            aggregate["mean_" + key] = sum(numbers) / len(numbers) if numbers else None
        recovered = sorted(r["task"] for r in values if r["task"] in failed_drafts and r.get("overall"))
        aggregate.update(recovered_tasks=recovered, recovery_denominator=len(failed_drafts),
                         recovery_rate=len(recovered)/len(failed_drafts) if failed_drafts else None)
        conditions[name] = aggregate
    by_task = {(r["task"], r["method"]): bool(r.get("overall")) for r in rows}
    paired = {}
    for name in CONDITIONS[1:]:
        wins, losses, ties = [], [], []
        for task in sorted({r["task"] for r in rows}):
            off, other = by_task.get((task, "off"), False), by_task.get((task, name), False)
            (ties if off == other else wins if other else losses).append(task)
        paired[name] = dict(wins=wins, losses=losses, ties=ties)
    return conditions, paired


def _evaluate(args):
    task_dirs = _discover(args)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    batch_dir = output_path(ROOT / "output" / ("rag_compare_" + stamp))
    batch_dir.mkdir(parents=True)
    config = load_config(args.config, frozen=True, allow_external=True)
    config_path = batch_dir / "config.json"
    write_json(config_path, config)
    _, runtime = load_runtime(args.rag_runtime)
    runtime_path = batch_dir / "rag_runtime.json"
    write_json(runtime_path, runtime)
    base = load_policy(args.policy)
    # Fail before generating a dataset of drafts if the selected release is invalid.
    Retrieval(dict(base, rag_enabled=True, rag_mode='hybrid'), runtime_path)
    policies = {}
    for name in (*CONDITIONS, "draft"):
        policy = dict(base, rag_enabled=name in ("bm25", "hybrid"),
                      rag_mode="bm25" if name == "bm25" else "hybrid")
        if name == "draft":
            policy["max_repairs"] = 0
        policies[name] = batch_dir / (name + "_policy.json")
        write_json(policies[name], policy)
    timeout = max(PER_TASK_TIMEOUT, config['hls']['total_timeout_seconds'] + 60)
    summary = dict(schema_version=2, protocol='fixed_draft_v2', batch_id=batch_dir.name, workers=args.workers,
                   task_count=len(task_dirs), status="running", results=[], drafts=[],
                   common_policy=base, config=config, rag_runtime=runtime,
                   conditions=list(CONDITIONS), two_phase_fixed_draft=True,
                   timing_scope="draft separately; conditions include initial revalidation, retrieval and repairs",
                   started_at=datetime.now(timezone.utc).isoformat())
    summary_file = batch_dir / "summary.json"
    def flush():
        write_json(summary_file, summary)
    def execute(task, condition, source=None):
        try:
            return _entry(task, batch_dir/task.name/condition, str(config_path), condition,
                          policies[condition], runtime_path, source, timeout=timeout)
        except Exception as error:
            return dict(task=task.name, method=condition, stage="runner_error", ok=False,
                        reason=type(error).__name__, message=str(error))
    flush()
    started = time.monotonic()
    frozen_drafts = {}
    frozen_hashes = {str(path): file_sha256(path) for path in [config_path, runtime_path, *policies.values()]}
    # Separate generation from all three repair conditions, including off.
    for task in task_dirs:
        row = execute(task, "draft")
        draft = _valid_draft(batch_dir/task.name/"draft")
        if draft is not None:
            frozen = batch_dir/task.name/"initial.cpp"
            frozen.write_bytes(draft.read_bytes())
            frozen_drafts[task.name] = frozen
            row["source_sha256"] = file_sha256(frozen)
            frozen_hashes[str(frozen)] = row['source_sha256']
        row["valid_source"] = draft is not None
        summary["drafts"].append(row)
        flush()
    jobs = []
    for task in task_dirs:
        if task.name not in frozen_drafts:
            for name in CONDITIONS:
                summary["results"].append(dict(task=task.name, method=name, ok=False,
                                               stage="excluded", stop_reason="no_valid_initial_source",
                                               overall=False, api_requests=0, elapsed_seconds=None))
        else:
            for name in CONDITIONS:
                jobs.append((task, name, frozen_drafts[task.name]))
    flush()
    # All conditions share the same queue and concurrency, with workers=1 by default.
    summary['frozen_snapshot_sha256'] = frozen_hashes
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(execute, *job): job for job in jobs}
        for future in as_completed(futures):
            row = future.result()
            summary["results"].append(row)
            flush()
            print(json.dumps({k: row.get(k) for k in ("task", "method", "ok", "stop_reason")}), flush=True)
    summary["comparison"], summary["paired_vs_off"] = summarize(summary["results"], summary["drafts"])
    # Preserve infrastructure failures; never report a broken harness as a clean run.
    incomplete = any(r.get("stage") == "runner_error" or
                     (r.get("stage") == "validated" and not r.get("checks"))
                     for r in summary["results"] + summary["drafts"])
    fatal = any(r.get("exit_code", 0) != 0 for r in summary["results"] + summary["drafts"])
    changed = [name for name, expected in frozen_hashes.items()
               if not Path(name).is_file() or file_sha256(Path(name)) != expected]
    if changed:
        summary['changed_snapshots'] = changed
        incomplete = True
    summary.update(status="completed_with_failures" if incomplete or fatal else "completed",
                   elapsed_seconds=round(time.monotonic()-started, 3),
                   finished_at=datetime.now(timezone.utc).isoformat())
    flush()
    print(json.dumps({"summary": str(summary_file), "comparison": summary["comparison"],
                      "paired_vs_off": summary["paired_vs_off"]}, ensure_ascii=False))
    return 1 if incomplete else 0


if __name__ == "__main__":
    raise SystemExit(main())
