#!/usr/bin/env python3
"""Run independent end-to-end RAG-off Workflow Skill ablations (S0-S3)."""

import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.core.contracts import digest
from agent.core.policy import load_policy
from agent.workflow.runtime import WorkflowSkills, input_paths as workflow_input_paths
from evaluation.lifecycle import evaluate as development_evaluation, output_path
from evaluation.task_io import load_manifest
from serve.inference import load_config, write_json
from tools.audit_skill_compare import EXPECTED_SKILLS, audit_run


DATASET = ROOT / "data/processed/bench4hls"
EXPECTED_DATASET_COMMIT = "7fac5b356b0383e9463995e2c6b13a5fee27a62d"
CONDITIONS = ("S0", "S1", "S2", "S3")
POLICY_FILES = {name: ROOT / f"agent/config/policy.skill-{name.lower()}.json" for name in CONDITIONS}
INFRA_CATEGORIES = {
    "api_network_or_timeout", "generation_timeout", "environment_error",
    "environment_or_dependency_error", "license_error", "tool_timeout",
    "local_evaluation_aborted", "workflow_configuration_error", "internal_error",
}


def _selection_ids(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(value.get("tasks"), list):
        ids = value["tasks"]
    elif isinstance(value.get("categories"), dict):
        ids = [task for tasks in value["categories"].values() for task in tasks]
    else:
        raise ValueError("Selection must contain tasks[] or categories{}")
    if not ids or any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("Selection task ids must be a nonempty unique string list")
    return ids


def _discover(args):
    dataset = Path(args.dataset).resolve()
    index = json.loads((dataset / "index.json").read_text(encoding="utf-8"))
    expected_ids = [f"Prob{number:03d}" for number in range(1, 171)]
    indexed_ids = [item.get("id") for item in index.get("tasks", [])]
    if (index.get("task_count") != 170 or indexed_ids != expected_ids
            or index.get("source", {}).get("commit") != EXPECTED_DATASET_COMMIT):
        raise ValueError("Bench4HLS dataset identity/count/order differs from the frozen protocol")
    if args.all:
        ids = expected_ids
    elif args.selection:
        ids = _selection_ids(args.selection)
    else:
        ids = args.task
    unknown = sorted(set(ids) - set(expected_ids))
    if unknown:
        raise ValueError("Unknown task ids: " + ", ".join(unknown))
    tasks = [dataset / item for item in ids]
    for task in tasks:
        for name in ("problem.txt", "task.json"):
            if not (task / name).is_file():
                raise ValueError("Task input missing: " + str(task / name))
    policies = []
    for task in [dataset / item for item in expected_ids]:
        manifest, _ = load_manifest(task / "task.json")
        policies.append(manifest["feedback_policy"])
    if len(set(policies)) != 1:
        raise ValueError("Frozen dataset mixes feedback policies")
    return dataset, index, tasks, policies[0]


def _policy(condition):
    value = load_policy(POLICY_FILES[condition])
    expected_flags = {
        "problem_contract_enabled": condition in {"S1", "S2", "S3"},
        "functional_selftest_enabled": condition in {"S2", "S3"},
        "synth_guard_enabled": condition == "S3",
    }
    if value.get("rag_enabled") is not False or value.get("skills_enabled") is not False:
        raise ValueError(condition + " must disable RAG and legacy repair rules")
    if value.get("max_repairs") != 2 or value.get("synth_guard_mode", "observe") != "observe":
        raise ValueError(condition + " violates the frozen repair/guard protocol")
    if any(value.get(name, False) != enabled for name, enabled in expected_flags.items()):
        raise ValueError(condition + " Workflow Skill flags do not match the S0-S3 matrix")
    if WorkflowSkills(value).enabled != EXPECTED_SKILLS[condition]:
        raise ValueError(condition + " Workflow Skill runtime differs from policy flags")
    return value


def _run_entry(task, directory, config, policy, workflow_root, timeout):
    command = [
        sys.executable, "-B", "-m", "agent.interface.entry",
        str(task / "problem.txt"), str(directory),
        "--config", str(config), "--policy", str(policy),
        "--task-manifest", str(task / "task.json"),
        "--workflow-skills-dir", str(workflow_root),
    ]
    run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=timeout)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "runner.stdout.log").write_text(run.stdout, encoding="utf-8")
    (directory / "runner.stderr.log").write_text(run.stderr, encoding="utf-8")
    receipt_path = directory / "result.json"
    if not receipt_path.is_file():
        raise RuntimeError("Agent did not produce result.json")
    return run.returncode, json.loads(receipt_path.read_text(encoding="utf-8"))


def _checks(value):
    value = value or {}
    return {name: value.get(name) == "passed" for name in ("compile", "run", "synthesize")}


def _metrics(task, condition, directory, exit_code, receipt, audit_errors):
    candidates = receipt.get("candidates") or []
    first = next((item for item in candidates if item.get("candidate_id") == 0), {})
    initial = _checks(first.get("checks"))
    final = _checks(receipt.get("checks"))
    usage = receipt.get("usage") or {}
    history = receipt.get("workflow_history") or []
    guards = [item for item in history if item.get("skill") == "synth-guard"]
    selftests = [item for item in history if item.get("skill") == "functional-selftest"
                 and "attempt" in item]
    contract_path = directory / "workflow/problem-contract/contract.json"
    contract_sha = digest(contract_path.read_bytes()) if contract_path.is_file() else None
    return {
        "task": task.name,
        "condition": condition,
        "directory": str(directory.resolve()),
        "exit_code": exit_code,
        "receipt_status": receipt.get("status"),
        "category": receipt.get("category"),
        "stop_reason": receipt.get("stop_reason"),
        "audit_errors": audit_errors,
        "initial_checks": initial,
        "initial_overall": initial["run"] and initial["synthesize"],
        "final_checks": final,
        "final_overall": final["run"] and final["synthesize"],
        "candidate0_sha256": first.get("source_sha256"),
        "selected_candidate": receipt.get("selected_candidate"),
        "contract_file_sha256": contract_sha,
        "generation_requests": receipt.get("generation_requests"),
        "workflow_requests": receipt.get("workflow_requests"),
        "model_requests": receipt.get("model_requests"),
        "api_requests_recorded": receipt.get("api_requests_recorded"),
        "total_tokens": usage.get("total_tokens"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "tool_calls": receipt.get("tool_calls"),
        "elapsed_seconds": receipt.get("elapsed_seconds"),
        "workflow_skills_enabled": receipt.get("workflow_skills_enabled"),
        "workflow_skills_used": receipt.get("workflow_skills_used"),
        "contract_ready": any(item.get("skill") == "problem-contract" and item.get("status") == "ready"
                              for item in history),
        "selftest_runs": len(selftests),
        "selftest_failures": sum(item.get("status") != "passed" for item in selftests),
        "synth_guard_runs": len(guards),
        "synth_guard_findings": sum(item.get("finding_count", 0) for item in guards),
    }


def _mean(values):
    values = [value for value in values if isinstance(value, (int, float))]
    return sum(values) / len(values) if values else None


def summarize(rows, conditions):
    aggregates = {}
    for condition in conditions:
        values = [row for row in rows if row["condition"] == condition]
        aggregate = {"tasks": len(values)}
        for phase in ("initial", "final"):
            for check in ("compile", "run", "synthesize"):
                key = phase + "_" + check
                aggregate[key] = sum(row[phase + "_checks"][check] for row in values)
            key = phase + "_overall"
            aggregate[key] = sum(bool(row[key]) for row in values)
            aggregate[key + "_rate"] = aggregate[key] / len(values) if values else None
        aggregate["recoveries"] = sum(not row["initial_overall"] and row["final_overall"] for row in values)
        for key in ("generation_requests", "workflow_requests", "model_requests", "total_tokens",
                    "tool_calls", "elapsed_seconds"):
            numbers = [row.get(key) for row in values]
            present = [value for value in numbers if isinstance(value, (int, float))]
            aggregate["mean_" + key] = _mean(present)
            aggregate["median_" + key] = statistics.median(present) if present else None
        aggregate["contract_ready"] = sum(row["contract_ready"] for row in values)
        aggregate["contract_failures"] = sum(row.get("category") in {"workflow_output_error", "problem_contract_ambiguous"}
                                             for row in values)
        aggregate["selftest_runs"] = sum(row["selftest_runs"] for row in values)
        aggregate["selftest_failures"] = sum(row["selftest_failures"] for row in values)
        aggregate["synth_guard_runs"] = sum(row["synth_guard_runs"] for row in values)
        aggregate["synth_guard_findings"] = sum(row["synth_guard_findings"] for row in values)
        aggregates[condition] = aggregate
    indexed = {(row["task"], row["condition"]): row for row in rows}
    comparisons = {}
    for left, right in (("S0", "S1"), ("S1", "S2"), ("S2", "S3"), ("S0", "S3")):
        if left not in conditions or right not in conditions:
            continue
        wins, losses, ties = [], [], []
        tasks = sorted({row["task"] for row in rows if row["condition"] in {left, right}})
        for task in tasks:
            a, b = indexed.get((task, left)), indexed.get((task, right))
            if a is None or b is None:
                continue
            outcome = (bool(a["final_overall"]), bool(b["final_overall"]))
            (ties if outcome[0] == outcome[1] else wins if outcome[1] else losses).append(task)
        comparisons[f"{right}-{left}"] = {"wins": wins, "losses": losses, "ties": ties}
    return aggregates, comparisons


def _evaluate(args):
    dataset, index, tasks, feedback_policy = _discover(args)
    conditions = tuple(item.strip().upper() for item in args.conditions.split(",") if item.strip())
    if not conditions or any(item not in CONDITIONS for item in conditions) or len(set(conditions)) != len(conditions):
        raise ValueError("--conditions must be a unique comma-separated subset of S0,S1,S2,S3")
    if tuple(sorted(conditions, key=CONDITIONS.index)) != conditions:
        raise ValueError("Conditions must preserve S0,S1,S2,S3 order")
    config = load_config(args.config, frozen=True, allow_external=True)
    if config["model"].get("temperature") != 0.0 or config["model"].get("name") != "qwen38":
        raise ValueError("Skill ablation requires qwen38 with temperature=0.0")
    policies = {condition: _policy(condition) for condition in conditions}

    batch = Path(output_path(args.output)).resolve()
    batch.mkdir(parents=True, exist_ok=False)
    config_path = batch / "config.json"
    write_json(config_path, config)
    policy_paths = {}
    for condition, policy in policies.items():
        path = batch / "policies" / (condition + ".json")
        write_json(path, policy)
        policy_paths[condition] = path

    workflow_root = batch / "workflow_skills"
    workflow_root.mkdir()
    source_root = Path(args.workflow_skills_dir).resolve()
    union = sorted({path for policy in policies.values() for path in workflow_input_paths(policy, source_root)})
    for source in union:
        target = workflow_root / source.relative_to(source_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    for condition, policy in policies.items():
        if WorkflowSkills(policy, workflow_root).sha256 != WorkflowSkills(policy, source_root).sha256:
            raise ValueError(condition + " Workflow Skill snapshot changed while freezing")

    frozen = [config_path, *policy_paths.values(), *sorted(workflow_root.rglob("*"))]
    frozen = [path for path in frozen if path.is_file()]
    frozen += [dataset / "index.json"]
    if args.selection:
        frozen.append(Path(args.selection).resolve())
    for task in tasks:
        manifest, names = load_manifest(task / "task.json")
        frozen.append(task / "task.json")
        frozen.extend(task / name for name in names)
    frozen_hashes = {str(path.resolve()): digest(path.read_bytes()) for path in sorted(set(frozen))}
    summary = {
        "schema_version": 1,
        "protocol": "rag-off-independent-e2e-skill-ablation-v1",
        "status": "running",
        "benchmark_label": "frozen benchmark replay",
        "dataset": str(dataset),
        "dataset_source_commit": index["source"]["commit"],
        "feedback_policy": feedback_policy,
        "task_ids": [task.name for task in tasks],
        "task_count": len(tasks),
        "conditions": list(conditions),
        "workers": 1,
        "config": config,
        "policies": policies,
        "policy_files": {name: str(path.resolve()) for name, path in policy_paths.items()},
        "workflow_skills_root": str(workflow_root.resolve()),
        "frozen_snapshot_sha256": frozen_hashes,
        "results": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    summary_path = batch / "summary.json"
    write_json(summary_path, summary)
    timeout = max(args.per_task_timeout, config["hls"]["total_timeout_seconds"] + 60)
    started = time.monotonic()
    protocol_error = None
    rows_by_task = {}
    for task in tasks:
        rows_by_task[task.name] = {}
        for condition in conditions:
            directory = batch / "runs" / task.name / condition
            try:
                exit_code, receipt = _run_entry(task, directory, config_path, policy_paths[condition],
                                                workflow_root, timeout)
                errors = audit_run(directory, condition, policies[condition])
                row = _metrics(task, condition, directory, exit_code, receipt, errors)
            except subprocess.TimeoutExpired as error:
                row = {"task": task.name, "condition": condition, "directory": str(directory.resolve()),
                       "runner_error": "subprocess_timeout", "message": str(error), "final_overall": False,
                       "initial_overall": False, "audit_errors": ["entry subprocess timed out"]}
                errors = row["audit_errors"]
            except Exception as error:
                row = {"task": task.name, "condition": condition, "directory": str(directory.resolve()),
                       "runner_error": type(error).__name__, "message": str(error), "final_overall": False,
                       "initial_overall": False, "audit_errors": [str(error)]}
                errors = row["audit_errors"]
            summary["results"].append(row)
            rows_by_task[task.name][condition] = row
            write_json(summary_path, summary)
            print(json.dumps({key: row.get(key) for key in
                              ("task", "condition", "receipt_status", "category", "final_overall", "audit_errors")},
                             ensure_ascii=False), flush=True)
            if errors:
                protocol_error = f"{task.name}/{condition}: evidence audit failed"
                break
            if row.get("category") in INFRA_CATEGORIES:
                protocol_error = f"{task.name}/{condition}: infrastructure failure {row['category']}"
                break
            if condition in {"S2", "S3"} and "S1" in rows_by_task[task.name]:
                reference = rows_by_task[task.name]["S1"]
                if (reference.get("candidate0_sha256") and row.get("candidate0_sha256")
                        and reference["candidate0_sha256"] != row["candidate0_sha256"]):
                    protocol_error = f"{task.name}: candidate 0 differs between S1 and {condition}"
                    break
                if (reference.get("contract_file_sha256") and row.get("contract_file_sha256")
                        and reference["contract_file_sha256"] != row["contract_file_sha256"]):
                    protocol_error = f"{task.name}: contract differs between S1 and {condition}"
                    break
        if protocol_error:
            break

    changed = [path for path, expected in frozen_hashes.items()
               if not Path(path).is_file() or digest(Path(path).read_bytes()) != expected]
    if changed:
        summary["changed_snapshots"] = changed
        protocol_error = protocol_error or "frozen experiment inputs changed"
    summary["comparison"], summary["paired_deltas"] = summarize(summary["results"], conditions)
    if protocol_error:
        summary["protocol_error"] = protocol_error
    task_failures = any(not row.get("final_overall", False) for row in summary["results"])
    summary.update(
        status="invalid" if protocol_error else "completed_with_task_failures" if task_failures else "completed",
        elapsed_seconds=round(time.monotonic() - started, 3),
        finished_at=datetime.now(timezone.utc).isoformat(),
    )
    write_json(summary_path, summary)
    print(json.dumps({"summary": str(summary_path), "status": summary["status"],
                      "comparison": summary["comparison"], "paired_deltas": summary["paired_deltas"]},
                     ensure_ascii=False))
    return 1 if protocol_error else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true", help="Run all frozen Prob001-Prob170 tasks")
    selection.add_argument("--selection", help="Explicit JSON with tasks[] or categories{}")
    selection.add_argument("--task", action="append", help="Explicit task id; repeat for multiple tasks")
    parser.add_argument("--conditions", default="S0,S1,S2,S3")
    parser.add_argument("--workers", type=int, choices=(1,), default=1,
                        help="Frozen protocol permits one worker only")
    parser.add_argument("--dataset", default=str(DATASET))
    parser.add_argument("--config", default=str(ROOT / "serve/runtime.rag-eval.json"))
    parser.add_argument("--workflow-skills-dir", default=str(ROOT / "skill"))
    parser.add_argument("--per-task-timeout", type=int, default=720)
    parser.add_argument("--output", help="New batch directory under output/")
    args = parser.parse_args()
    if args.output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        args.output = str(ROOT / "output" / ("skill_compare_" + stamp))
    args.policy_files = [str(path) for path in POLICY_FILES.values()]
    return development_evaluation(args, _evaluate, output=args.output)


if __name__ == "__main__":
    raise SystemExit(main())
