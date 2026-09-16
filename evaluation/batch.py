import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

from serve.inference import Failure, ROOT, project_path, write_json
from evaluation.lifecycle import evaluate as development_evaluation, output_path


def main():
    parser = argparse.ArgumentParser(description="Sequential HLS-Eval development batch baseline")
    parser.add_argument("dataset", help="Project-relative directory containing task.json files")
    parser.add_argument("--limit", type=int, default=0, help="Maximum tasks; zero means all")
    parser.add_argument("--cpu-only", action="store_true")
    parser.add_argument("--config", help="Defaults to the untracked serve/runtime.local.json when present")
    parser.add_argument("--repair-attempts", type=int, default=0, choices=range(6))
    parser.add_argument("--no-api", action="store_true", help="Validate task-local source paths only when supplied; not supported for dataset baseline")
    args = parser.parse_args()
    return development_evaluation(args, _evaluate)


def _evaluate(args):
    try:
        dataset = project_path(args.dataset)
        manifests = sorted(dataset.rglob("task.json"))
        if args.limit < 0 or not manifests:
            raise Failure("input_error", "Dataset has no task.json files or limit is invalid")
        if args.limit:
            manifests = manifests[:args.limit]
        if args.no_api:
            raise Failure("input_error", "Dataset batch baseline requires one API generation per task")
        batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        batch_dir = output_path(project_path("output/batches") / batch_id)
        batch_dir.mkdir(parents=True, exist_ok=False)
        summary = {"schema_version": 1, "batch_id": batch_id, "dataset": str(dataset.relative_to(ROOT)), "task_count": len(manifests), "status": "running", "tasks": [], "api_requests_expected": len(manifests), "cpu_only_requested": args.cpu_only}
        write_json(batch_dir / "summary.json", summary)
        started = time.monotonic()
        for index, manifest in enumerate(manifests, 1):
            command = [sys.executable, "-m", "evaluation.single_task", str(manifest.relative_to(ROOT)), "--repair-attempts", str(args.repair_attempts)]
            if args.config:
                command.extend(["--config", args.config])
            if args.cpu_only:
                command.append("--cpu-only")
            task_started = time.monotonic()
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
            lines = [line for line in result.stdout.splitlines() if line.strip()]
            try:
                record = json.loads(lines[-1])
            except (IndexError, json.JSONDecodeError):
                record = {"status": "failed", "category": "batch_runner_error", "stdout_tail": result.stdout[-1000:], "stderr_tail": result.stderr[-1000:]}
            entry = {"index": index, "task_manifest": str(manifest.relative_to(ROOT)), "elapsed_seconds": round(time.monotonic() - task_started, 3), "exit_code": result.returncode, **record}
            if record.get("result"):
                try:
                    task_result = json.loads((ROOT / record["result"]).read_text(encoding="utf-8"))
                    entry["api_requests"] = task_result.get("api_requests_recorded", 0)
                    entry["model"] = task_result.get("stages", {}).get("generation", {}).get("model", {}).get("name")
                    entry["checks"] = task_result.get("checks")
                except (OSError, ValueError, TypeError):
                    entry["api_requests"] = 0
            summary["tasks"].append(entry)
            write_json(batch_dir / "summary.json", summary)
            print(json.dumps(entry, ensure_ascii=False), flush=True)
        passed = sum(entry.get("status") == "passed" for entry in summary["tasks"])
        summary.update(status="passed" if passed == len(manifests) else "completed_with_failures", passed=passed, failed=len(manifests) - passed, elapsed_seconds=round(time.monotonic() - started, 3), api_requests_actual=sum(entry.get("api_requests", 0) for entry in summary["tasks"]))
        write_json(batch_dir / "summary.json", summary)
        print(json.dumps({"status": summary["status"], "passed": passed, "failed": summary["failed"], "summary": str((batch_dir / "summary.json").relative_to(ROOT))}, ensure_ascii=False))
        return 0 if passed == len(manifests) else 1
    except Failure as error:
        print(error.category + ": " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
